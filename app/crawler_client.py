# -*- coding: utf-8 -*-
"""
爬蟲服務 HTTP 客戶端

主程式透過此模組以 HTTP API 呼叫獨立的爬蟲 Cloud Run 服務 (content-crawler)。
需在環境變數中設定：
  CRAWLER_SERVICE_URL  爬蟲服務的 Cloud Run URL（例如 https://content-crawler-xxx.run.app）
  CRAWLER_API_KEY      與爬蟲服務相同的 API Key（存於 Secret Manager）

回傳格式統一：
  成功: {"status": "success", "url": "...", "title": "...", "content": "...", "length": N}
  略過: {"status": "skipped", "url": "...", "error": "..."}
  失敗: {"status": "failed",  "url": "...", "error": "..."}
"""
import os

import requests

DEFAULT_TIMEOUT = 300  # seconds


def _service_url() -> str:
    return os.environ.get("CRAWLER_SERVICE_URL", "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("CRAWLER_API_KEY", "")


def _headers() -> dict:
    return {"X-API-Key": _api_key(), "Content-Type": "application/json"}


def _handle_response(resp, url: str) -> dict:
    """解析 API 回應，處理非 JSON 與非預期 HTTP 狀態碼。"""
    if resp.status_code == 401:
        return {"status": "failed", "url": url,
                "error": "爬蟲服務金鑰驗證失敗 (401)，請檢查 CRAWLER_API_KEY。"}
    try:
        return resp.json()
    except ValueError:
        return {"status": "failed", "url": url,
                "error": f"爬蟲服務回傳非 JSON 內容 (HTTP {resp.status_code}): {resp.text[:200]}"}


def scrape_via_api(url: str, use_gemini: bool = False,
                   gemini_api_key: str = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """呼叫 POST /api/scrape 爬取單一網址。"""
    base = _service_url()
    if not base:
        return {"status": "failed", "url": url,
                "error": "CRAWLER_SERVICE_URL 未設定，無法呼叫爬蟲服務。"}

    payload = {"url": url, "use_gemini": bool(use_gemini)}
    if gemini_api_key:
        payload["gemini_api_key"] = gemini_api_key

    try:
        resp = requests.post(f"{base}/api/scrape", json=payload,
                             headers=_headers(), timeout=timeout)
    except requests.exceptions.Timeout:
        return {"status": "failed", "url": url, "error": f"爬蟲服務逾時 ({timeout}s)。"}
    except requests.exceptions.RequestException as e:
        return {"status": "failed", "url": url, "error": f"無法連線爬蟲服務: {e}"}

    return _handle_response(resp, url)


def scrape_batch_via_api(urls: list, use_gemini: bool = False,
                         gemini_api_key: str = None,
                         timeout: int = DEFAULT_TIMEOUT * 5) -> dict:
    """呼叫 POST /api/scrape/batch，一次送出多個 URL（最多 20 個）。

    回傳格式：
      {"results": [...], "total": N, "succeeded": N, "failed": N}
    """
    base = _service_url()
    if not base:
        return {"results": [], "total": 0, "succeeded": 0, "failed": len(urls),
                "error": "CRAWLER_SERVICE_URL 未設定。"}

    payload = {"urls": urls, "use_gemini": bool(use_gemini)}
    if gemini_api_key:
        payload["gemini_api_key"] = gemini_api_key

    try:
        resp = requests.post(f"{base}/api/scrape/batch", json=payload,
                             headers=_headers(), timeout=timeout)
    except requests.exceptions.Timeout:
        return {"results": [], "total": len(urls), "succeeded": 0, "failed": len(urls),
                "error": f"爬蟲服務批次逾時 ({timeout}s)。"}
    except requests.exceptions.RequestException as e:
        return {"results": [], "total": len(urls), "succeeded": 0, "failed": len(urls),
                "error": f"無法連線爬蟲服務: {e}"}

    if resp.status_code == 401:
        return {"results": [], "total": len(urls), "succeeded": 0, "failed": len(urls),
                "error": "爬蟲服務金鑰驗證失敗 (401)，請檢查 CRAWLER_API_KEY。"}

    try:
        return resp.json()
    except ValueError:
        return {"results": [], "total": len(urls), "succeeded": 0, "failed": len(urls),
                "error": f"非 JSON 回應 (HTTP {resp.status_code}): {resp.text[:200]}"}
