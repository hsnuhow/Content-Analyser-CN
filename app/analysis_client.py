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


# 終態詞彙（server 業務狀態機真正會回的終態）。消費端只應把這些當終態寫回。
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _query_status(path: str, job_id: str, timeout: int) -> dict:
    """查詢非同步 job 狀態，明確區分三類，避免把『傳輸層失敗』當成『job 失敗』：
      - 傳輸層失敗（逾時/連線/401/5xx/未設定 URL）→ {"status":"unavailable"}（暫時，消費端應保持原狀）
      - server 回 404（job 不存在）→ {"status":"not_found"}（真的遺失）
      - 其餘 → 透傳 server JSON（含 completed/failed/cancelled/running/pending）
    """
    base = _base_url()
    if not base:
        return {"status": "unavailable", "error": "ANALYSIS_SERVICE_URL 未設定"}
    try:
        resp = requests.get(f"{base}{path}", headers=_headers(), timeout=timeout)
        if resp.status_code == 404:
            return {"status": "not_found", "error": f"找不到 job_id：{job_id}"}
        if resp.status_code == 401:
            return {"status": "unavailable", "error": "金鑰驗證失敗（401）"}
        if resp.status_code >= 500:
            return {"status": "unavailable", "error": f"服務暫時不可用（{resp.status_code}）"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"status": "unavailable", "error": "查詢逾時"}
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}


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


def suggest_filters(contents: list, max_candidates: int = 60,
                    timeout: int = 120) -> dict:
    """同步呼叫分析服務找候選垃圾詞（三信號）。
    回傳 {"candidates":[...], "n_docs", "by_source"} 或 {"error": "..."}。"""
    base = _base_url()
    if not base:
        return {"error": "ANALYSIS_SERVICE_URL 未設定。"}
    try:
        resp = requests.post(
            f"{base}/api/suggest-filters",
            json={"contents": contents, "max_candidates": max_candidates},
            headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "分析服務金鑰驗證失敗（401）。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"分析逾時（{timeout}s）。"}
    except Exception as e:
        return {"error": f"無法連線至分析服務：{e}"}


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


def submit_image_analysis(report_title: str, images: list,
                          llm_provider: str, llm_model: str, llm_api_key: str,
                          temperature: float = 0.3,
                          timeout: int = DEFAULT_TIMEOUT) -> dict:
    """提交影像視覺分析任務（非同步，階段②）。回傳 {"job_id": ...} 或 {"error": ...}。"""
    base = _base_url()
    if not base:
        return {"error": "ANALYSIS_SERVICE_URL 未設定，無法提交影像分析任務。"}
    payload = {
        "report_title": report_title,
        "images": images,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "llm_api_key": llm_api_key,
        "temperature": temperature,
    }
    try:
        resp = requests.post(f"{base}/api/analyse-images", json=payload,
                             headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "分析服務金鑰驗證失敗（401），請確認 ANALYSIS_API_KEY。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"提交影像分析任務逾時（{timeout}s）。"}
    except Exception as e:
        return {"error": f"無法連線至分析服務：{e}"}


def get_image_analysis_status(job_id: str, timeout: int = POLL_TIMEOUT) -> dict:
    """查詢影像視覺分析任務進度與結果（result_markdown）。"""
    return _query_status(f"/api/analyse-images/{job_id}", job_id, timeout)


def submit_combined(report_title: str, text_markdown: str, visual_markdown: str,
                    llm_provider: str, llm_model: str, llm_api_key: str,
                    topic: str = "", temperature: float = 0.3,
                    timeout: int = DEFAULT_TIMEOUT) -> dict:
    """提交整合報告任務（非同步，階段③）。回傳 {"job_id": ...} 或 {"error": ...}。"""
    base = _base_url()
    if not base:
        return {"error": "ANALYSIS_SERVICE_URL 未設定，無法提交整合任務。"}
    payload = {
        "report_title": report_title,
        "text_markdown": text_markdown,
        "visual_markdown": visual_markdown,
        "topic": topic or report_title,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "llm_api_key": llm_api_key,
        "temperature": temperature,
    }
    try:
        resp = requests.post(f"{base}/api/synthesize-combined", json=payload,
                             headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "分析服務金鑰驗證失敗（401），請確認 ANALYSIS_API_KEY。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"提交整合任務逾時（{timeout}s）。"}
    except Exception as e:
        return {"error": f"無法連線至分析服務：{e}"}


def get_combined_status(job_id: str, timeout: int = POLL_TIMEOUT) -> dict:
    """查詢整合報告任務進度與結果（result_markdown）。"""
    return _query_status(f"/api/synthesize-combined/{job_id}", job_id, timeout)


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
    （status 可能為 server 真實狀態，或 unavailable/not_found；見 _query_status）
    """
    return _query_status(f"/api/analyse/{job_id}", job_id, timeout)


def submit_audience(report_title: str, source_markdown: str, experts: list,
                    llm_provider: str, llm_model: str, llm_api_key: str,
                    temperature: float = 0.4,
                    timeout: int = DEFAULT_TIMEOUT) -> dict:
    """提交延伸行動報告任務（非同步）。experts=[{slug,label,prompt,playbook}]。
    回傳 {"job_id": ...} 或 {"error": ...}。"""
    base = _base_url()
    if not base:
        return {"error": "ANALYSIS_SERVICE_URL 未設定，無法提交延伸報告任務。"}
    payload = {
        "report_title": report_title,
        "source_markdown": source_markdown,
        "experts": experts,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "llm_api_key": llm_api_key,
        "temperature": temperature,
    }
    try:
        resp = requests.post(f"{base}/api/audience-reports", json=payload,
                             headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "分析服務金鑰驗證失敗（401），請確認 ANALYSIS_API_KEY。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"提交延伸報告任務逾時（{timeout}s）。"}
    except Exception as e:
        return {"error": f"無法連線至分析服務：{e}"}


def get_audience_status(job_id: str, timeout: int = POLL_TIMEOUT) -> dict:
    """查詢延伸報告任務進度與結果（completed 時含 audience_reports）。"""
    return _query_status(f"/api/audience-reports/{job_id}", job_id, timeout)


def trigger_kb_index(expert_slug: str, timeout: int = 120) -> dict:
    """請 analysis-pipeline 重新索引某專家的參考文件（切塊 + 系統 SA embedding → kb_chunks）。
    回 {"indexed": N} 或 {"error": ...}。"""
    base = _base_url()
    if not base:
        return {"error": "ANALYSIS_SERVICE_URL 未設定"}
    try:
        resp = requests.post(f"{base}/api/kb/index",
                             json={"expert_slug": expert_slug},
                             headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "分析服務金鑰驗證失敗（401）"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"索引逾時（{timeout}s），文件量大時可稍後重試。"}
    except Exception as e:
        return {"error": f"無法連線至分析服務：{e}"}
