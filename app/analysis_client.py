# -*- coding: utf-8 -*-
"""
Analysis Pipeline HTTP 客戶端

主程式透過此模組呼叫 analysis-pipeline 服務。
需設定環境變數：
  ANALYSIS_SERVICE_URL  分析服務的 Cloud Run URL
  ANALYSIS_API_KEY      分析服務的存取金鑰（與 Secret Manager 相同）
"""
import os
import requests

DEFAULT_TIMEOUT = 30   # 提交任務用（非同步，很快回應）
POLL_TIMEOUT = 10      # 查詢狀態用


def _base_url() -> str:
    return os.environ.get("ANALYSIS_SERVICE_URL", "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("ANALYSIS_API_KEY", "")


def _headers() -> dict:
    return {"X-API-Key": _api_key(), "Content-Type": "application/json"}


def check_health(timeout: int = POLL_TIMEOUT) -> dict:
    """呼叫 analysis-pipeline /health，回傳狀態 dict。"""
    base = _base_url()
    if not base:
        return {"status": "unknown", "error": "ANALYSIS_SERVICE_URL 未設定"}
    try:
        resp = requests.get(f"{base}/health", timeout=timeout)
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"status": "unreachable", "error": "無法連線至分析服務"}
    except requests.exceptions.Timeout:
        return {"status": "unreachable", "error": f"連線逾時 ({timeout}s)"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def submit_analysis(report_title: str, contents: list,
                    llm_provider: str, llm_model: str, llm_api_key: str,
                    temperature: float = 0.3, thinking: bool = False,
                    search_extent: bool = True,
                    max_output_tokens: int = 8192, top_p=None,
                    input_scale: str = "standard",
                    timeout: int = DEFAULT_TIMEOUT) -> dict:
    """提交分析任務（非同步）。

    回傳：{"job_id": "...", "status": "pending"}
    失敗：{"error": "..."}
    """
    base = _base_url()
    if not base:
        return {"error": "ANALYSIS_SERVICE_URL 未設定，無法提交分析任務。"}

    payload = {
        "report_title": report_title,
        "contents": contents,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "llm_api_key": llm_api_key,
        "temperature": temperature,
        "thinking": thinking,
        "search_extent": search_extent,
        "max_output_tokens": max_output_tokens,
        "top_p": top_p,
        "input_scale": input_scale,
    }
    try:
        resp = requests.post(
            f"{base}/api/analyse",
            json=payload,
            headers=_headers(),
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"error": "分析服務金鑰驗證失敗（401），請確認 ANALYSIS_API_KEY。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"提交分析任務逾時（{timeout}s）。"}
    except Exception as e:
        return {"error": f"無法連線至分析服務：{e}"}


def cancel_analysis(job_id: str, timeout: int = POLL_TIMEOUT) -> dict:
    """請求取消分析任務（合作式）。回傳 {"status": ...} 或 {"error": ...}。"""
    base = _base_url()
    if not base:
        return {"error": "ANALYSIS_SERVICE_URL 未設定。"}
    if not job_id:
        return {"error": "缺少 job_id。"}
    try:
        resp = requests.post(f"{base}/api/analyse/{job_id}/cancel",
                             headers=_headers(), timeout=timeout)
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": "取消請求逾時。"}
    except Exception as e:
        return {"error": str(e)}


def cleanup_analysis_jobs(days: int = 7, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """清除孤兒/陳舊分析任務文件。回傳 {"deleted": N} 或 {"error": ...}。"""
    base = _base_url()
    if not base:
        return {"error": "ANALYSIS_SERVICE_URL 未設定。"}
    try:
        resp = requests.post(f"{base}/api/analyse/cleanup", json={"days": days},
                             headers=_headers(), timeout=timeout)
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": "清理請求逾時。"}
    except Exception as e:
        return {"error": str(e)}


def get_job_status(job_id: str, timeout: int = POLL_TIMEOUT) -> dict:
    """查詢分析任務進度與結果。

    回傳：{"status": "...", "progress": N, "log": "...", ...}
    """
    base = _base_url()
    if not base:
        return {"status": "error", "error": "ANALYSIS_SERVICE_URL 未設定"}
    try:
        resp = requests.get(
            f"{base}/api/analyse/{job_id}",
            headers=_headers(),
            timeout=timeout,
        )
        if resp.status_code == 404:
            return {"status": "error", "error": f"找不到 job_id：{job_id}"}
        if resp.status_code == 401:
            return {"status": "error", "error": "金鑰驗證失敗（401）"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"status": "error", "error": "查詢逾時"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
