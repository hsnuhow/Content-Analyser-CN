# -*- coding: utf-8 -*-
"""
爬蟲服務 HTTP 客戶端

content-analyser 透過此模組呼叫 content-crawler：
  - check_crawler_health()：服務監控
  - submit_crawl_batch()：提交非同步批次爬取，回傳 job_id
  - get_crawl_status()：輪詢爬取進度與結果

需環境變數：CRAWLER_SERVICE_URL、CRAWLER_API_KEY
"""
import os
import requests

DEFAULT_TIMEOUT = 10
SUBMIT_TIMEOUT = 30


def _crawler_url() -> str:
    return os.environ.get("CRAWLER_SERVICE_URL", "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("CRAWLER_API_KEY", "")


def _headers() -> dict:
    return {"X-API-Key": _api_key(), "Content-Type": "application/json"}


def submit_crawl_batch(urls: list, use_gemini: bool = False,
                       gemini_api_key: str = None,
                       timeout: int = SUBMIT_TIMEOUT) -> dict:
    """提交非同步批次爬取。回傳 {"job_id": ...} 或 {"error": ...}。"""
    base = _crawler_url()
    if not base:
        return {"error": "CRAWLER_SERVICE_URL 未設定。"}
    payload = {"urls": urls, "use_gemini": bool(use_gemini)}
    if gemini_api_key:
        payload["gemini_api_key"] = gemini_api_key
    try:
        resp = requests.post(f"{base}/api/crawl/batch", json=payload,
                             headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "爬蟲服務金鑰驗證失敗（401）。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"提交爬取任務逾時（{timeout}s）。"}
    except Exception as e:
        return {"error": f"無法連線爬蟲服務：{e}"}


def get_crawl_status(job_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """查詢非同步爬取任務進度與結果。"""
    base = _crawler_url()
    if not base:
        return {"status": "error", "error": "CRAWLER_SERVICE_URL 未設定。"}
    try:
        resp = requests.get(f"{base}/api/crawl/{job_id}",
                            headers=_headers(), timeout=timeout)
        if resp.status_code == 404:
            return {"status": "error", "error": f"找不到 job_id：{job_id}"}
        if resp.status_code == 401:
            return {"status": "error", "error": "金鑰驗證失敗（401）。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"status": "error", "error": "查詢逾時。"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def submit_research(urls: list, timeout: int = SUBMIT_TIMEOUT) -> dict:
    """提交非同步「選擇器研究」（對失敗 URL）。回傳 {"job_id": ...} 或 {"error": ...}。"""
    base = _crawler_url()
    if not base:
        return {"error": "CRAWLER_SERVICE_URL 未設定。"}
    try:
        resp = requests.post(f"{base}/api/research", json={"urls": urls},
                             headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "爬蟲服務金鑰驗證失敗（401）。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"提交研究任務逾時（{timeout}s）。"}
    except Exception as e:
        return {"error": f"無法連線爬蟲服務：{e}"}


def get_research_status(job_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """查詢研究任務進度與結果（candidates / diagnoses）。"""
    base = _crawler_url()
    if not base:
        return {"status": "error", "error": "CRAWLER_SERVICE_URL 未設定。"}
    try:
        resp = requests.get(f"{base}/api/research/{job_id}",
                            headers=_headers(), timeout=timeout)
        if resp.status_code == 404:
            return {"status": "error", "error": f"找不到 job_id：{job_id}"}
        if resp.status_code == 401:
            return {"status": "error", "error": "金鑰驗證失敗（401）。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"status": "error", "error": "查詢逾時。"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def cancel_crawl(job_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """請求取消非同步爬取任務（合作式）。回傳 {"status": ...} 或 {"error": ...}。"""
    base = _crawler_url()
    if not base:
        return {"error": "CRAWLER_SERVICE_URL 未設定。"}
    if not job_id:
        return {"error": "缺少 job_id。"}
    try:
        resp = requests.post(f"{base}/api/crawl/{job_id}/cancel",
                             headers=_headers(), timeout=timeout)
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": "取消請求逾時。"}
    except Exception as e:
        return {"error": str(e)}


def cleanup_crawl_jobs(days: int = 7, timeout: int = SUBMIT_TIMEOUT) -> dict:
    """清除孤兒/陳舊爬取任務文件。回傳 {"deleted": N} 或 {"error": ...}。"""
    base = _crawler_url()
    if not base:
        return {"error": "CRAWLER_SERVICE_URL 未設定。"}
    try:
        resp = requests.post(f"{base}/api/crawl/cleanup", json={"days": days},
                             headers=_headers(), timeout=timeout)
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": "清理請求逾時。"}
    except Exception as e:
        return {"error": str(e)}


def check_crawler_health(timeout: int = DEFAULT_TIMEOUT) -> dict:
    """呼叫 content-crawler /health，回傳狀態 dict。

    回傳格式：
      正常: {"status": "ok", "service": "content-crawler", "version": "...", ...}
      異常: {"status": "unreachable" | "error" | "unknown", "error": "..."}
    """
    base = _crawler_url()
    if not base:
        return {"status": "unknown", "error": "CRAWLER_SERVICE_URL 未設定"}
    try:
        resp = requests.get(f"{base}/health", timeout=timeout)
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"status": "unreachable", "error": "無法連線至爬蟲服務"}
    except requests.exceptions.Timeout:
        return {"status": "unreachable", "error": f"連線逾時 ({timeout}s)"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
