# -*- coding: utf-8 -*-
"""
爬蟲服務 HTTP 客戶端

主程式不再內嵌爬蟲，改為透過 API 呼叫獨立的爬蟲 Cloud Run 服務 (content-crawler)。
存取需帶上 X-API-Key（與爬蟲服務的 CRAWLER_API_KEY 相同）。
"""
import os

import requests


def _service_url() -> str:
    return os.environ.get("CRAWLER_SERVICE_URL", "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("CRAWLER_API_KEY", "")


def scrape_via_api(url, use_gemini=False, gemini_api_key=None, timeout=300):
    """呼叫獨立爬蟲服務爬取單一網址，回傳與舊 HeadlessCrawler.scrape 相容的 dict。

    回傳格式：
      成功: {"status":"success","url","title","content","length"}
      其他: {"status":"failed"|"skipped","url","error"}
    """
    base = _service_url()
    if not base:
        return {"status": "failed", "url": url,
                "error": "CRAWLER_SERVICE_URL 未設定，無法呼叫獨立爬蟲服務。"}

    endpoint = f"{base}/api/scrape"
    headers = {"X-API-Key": _api_key(), "Content-Type": "application/json"}
    payload = {"url": url, "use_gemini": bool(use_gemini)}
    if gemini_api_key:
        payload["gemini_api_key"] = gemini_api_key

    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        return {"status": "failed", "url": url, "error": f"呼叫爬蟲服務逾時 ({timeout}s)。"}
    except requests.exceptions.RequestException as e:
        return {"status": "failed", "url": url, "error": f"呼叫爬蟲服務失敗: {e}"}

    if resp.status_code == 401:
        return {"status": "failed", "url": url, "error": "爬蟲服務金鑰驗證失敗 (401)。請檢查 CRAWLER_API_KEY。"}

    try:
        return resp.json()
    except ValueError:
        return {"status": "failed", "url": url,
                "error": f"爬蟲服務回傳非 JSON 內容 (HTTP {resp.status_code}): {resp.text[:200]}"}
