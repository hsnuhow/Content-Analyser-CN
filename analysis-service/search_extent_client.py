# -*- coding: utf-8 -*-
"""
search-extent HTTP 客戶端（analysis-pipeline → search-extent）

分析流程末段用語意群的 top 關鍵字呼叫 search-extent /api/expand，
取得真實「關聯關鍵字 + 搜尋量」作為報告 §7 延伸的接地資料。

需環境變數（部署時注入）：
  SEARCH_EXTENT_SERVICE_URL  search-extent 的 Cloud Run URL
  SEARCH_EXTENT_API_KEY      呼叫金鑰

設計原則：**絕不讓失敗拖垮分析**——任何錯誤回傳 {"error": ...}，呼叫端略過即可。
未設定 URL/Key 時 is_enabled() 為 False，pipeline 直接跳過（純 LLM §7）。
"""
import os
import requests

DEFAULT_TIMEOUT = 30


def _base_url() -> str:
    return os.environ.get("SEARCH_EXTENT_SERVICE_URL", "").rstrip("/")


def _api_key() -> str:
    return os.environ.get("SEARCH_EXTENT_API_KEY", "")


def is_enabled() -> bool:
    """URL 與 Key 皆設定時才啟用。"""
    return bool(_base_url() and _api_key())


def expand(seeds, language_id=None, geo_ids=None, limit=60,
           timeout: int = DEFAULT_TIMEOUT) -> dict:
    """呼叫 search-extent /api/expand。成功回 {status, seeds, count, ideas}；失敗回 {error}。"""
    base = _base_url()
    if not base:
        return {"error": "SEARCH_EXTENT_SERVICE_URL 未設定"}
    payload = {"seeds": seeds, "limit": limit}
    if language_id:
        payload["language_id"] = language_id
    if geo_ids:
        payload["geo_ids"] = geo_ids
    try:
        resp = requests.post(
            f"{base}/api/expand", json=payload,
            headers={"X-API-Key": _api_key(), "Content-Type": "application/json"},
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"error": "search-extent 金鑰驗證失敗（401）"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"search-extent 逾時（{timeout}s）"}
    except Exception as e:
        return {"error": f"search-extent 連線失敗：{e}"}
