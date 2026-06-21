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


# 終態詞彙（crawler 業務狀態機真正會回的終態）。消費端只應把這些當終態寫回。
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _query_status(path: str, job_id: str, timeout: int) -> dict:
    """查詢非同步 job 狀態，區分『傳輸層失敗(unavailable，暫時、勿當終態)』/『404(not_found)』/server 真實狀態。
    避免一次網路抖動或 crawler 端短暫 503 就把仍在跑的 job 寫成永久 failed。"""
    base = _crawler_url()
    if not base:
        return {"status": "unavailable", "error": "CRAWLER_SERVICE_URL 未設定。"}
    try:
        resp = requests.get(f"{base}{path}", headers=_headers(), timeout=timeout)
        if resp.status_code == 404:
            return {"status": "not_found", "error": f"找不到 job_id：{job_id}"}
        if resp.status_code == 401:
            return {"status": "unavailable", "error": "金鑰驗證失敗（401）。"}
        if resp.status_code >= 500:
            return {"status": "unavailable", "error": f"服務暫時不可用（{resp.status_code}）"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"status": "unavailable", "error": "查詢逾時。"}
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}


def _request_json(method: str, path: str, *, payload=None,
                  timeout: int = DEFAULT_TIMEOUT, timeout_msg: str = "請求逾時。") -> dict:
    """共用 HTTP 包裝（給 submit / cancel / cleanup 等動作用）：
      base 未設→error；POST 帶 json，GET 不帶；401→金鑰錯；逾時→timeout_msg；其餘例外→連線錯。
    回傳 server JSON 或 {"error": ...}。把原本散在 5 個函式、一模一樣的 try/except 收斂成一份。
    （狀態查詢不走這裡——它需要區分 unavailable / not_found 三態，見 _query_status。）"""
    base = _crawler_url()
    if not base:
        return {"error": "CRAWLER_SERVICE_URL 未設定。"}
    try:
        if method == "POST":
            resp = requests.post(f"{base}{path}", json=payload, headers=_headers(), timeout=timeout)
        else:
            resp = requests.get(f"{base}{path}", headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "爬蟲服務金鑰驗證失敗（401）。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": timeout_msg}
    except Exception as e:
        return {"error": f"無法連線爬蟲服務：{e}"}


def submit_crawl_batch(urls: list, use_gemini: bool = False,
                       gemini_api_key: str = None,
                       timeout: int = SUBMIT_TIMEOUT,
                       force_listing: bool = False) -> dict:
    """提交非同步批次爬取。回傳 {"job_id": ...} 或 {"error": ...}。
    force_listing=True：強制爬取被判為列表/商品頁的 URL（不略過）。"""
    payload = {"urls": urls, "use_gemini": bool(use_gemini)}
    if force_listing:
        payload["force_listing"] = True
    if gemini_api_key:
        payload["gemini_api_key"] = gemini_api_key
    return _request_json("POST", "/api/crawl/batch", payload=payload, timeout=timeout,
                         timeout_msg=f"提交爬取任務逾時（{timeout}s）。")


def get_crawl_status(job_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """查詢非同步爬取任務進度與結果。"""
    return _query_status(f"/api/crawl/{job_id}", job_id, timeout)


def submit_research(urls: list, timeout: int = SUBMIT_TIMEOUT) -> dict:
    """提交非同步「選擇器研究」（對失敗 URL）。回傳 {"job_id": ...} 或 {"error": ...}。"""
    return _request_json("POST", "/api/research", payload={"urls": urls}, timeout=timeout,
                         timeout_msg=f"提交研究任務逾時（{timeout}s）。")


def get_research_status(job_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """查詢研究任務進度與結果（candidates / diagnoses）。"""
    return _query_status(f"/api/research/{job_id}", job_id, timeout)


def submit_extract_images(urls: list, timeout: int = SUBMIT_TIMEOUT) -> dict:
    """提交非同步「主文大圖擷取」。回傳 {"job_id": ...} 或 {"error": ...}。"""
    return _request_json("POST", "/api/extract-images", payload={"urls": urls}, timeout=timeout,
                         timeout_msg=f"提交影像擷取任務逾時（{timeout}s）。")


def get_extract_images_status(job_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """查詢影像擷取任務進度與結果（每 URL 的大圖清單）。"""
    return _query_status(f"/api/extract-images/{job_id}", job_id, timeout)


def cancel_crawl(job_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """請求取消非同步爬取任務（合作式）。回傳 {"status": ...} 或 {"error": ...}。"""
    if not job_id:
        return {"error": "缺少 job_id。"}
    return _request_json("POST", f"/api/crawl/{job_id}/cancel", timeout=timeout,
                         timeout_msg="取消請求逾時。")


def cleanup_crawl_jobs(days: int = 7, timeout: int = SUBMIT_TIMEOUT) -> dict:
    """清除孤兒/陳舊爬取任務文件。回傳 {"deleted": N} 或 {"error": ...}。"""
    return _request_json("POST", "/api/crawl/cleanup", payload={"days": days}, timeout=timeout,
                         timeout_msg="清理請求逾時。")


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
