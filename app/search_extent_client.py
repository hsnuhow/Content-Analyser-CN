# -*- coding: utf-8 -*-
"""
search-extent 服務客戶端（搜尋情報層）。

控制平面（content-analyser）透過此 client 呼叫 search-extent，單向、X-API-Key。
目前用到的子功能：B 供給側·內容發現（/api/discover）。
需環境變數：
  SEARCH_EXTENT_SERVICE_URL  search-extent 的 Cloud Run URL
  SEARCH_EXTENT_API_KEY      search-extent 存取金鑰（'expand' 權限族）
"""
import os
import requests


def _base_url() -> str:
    return os.environ.get("SEARCH_EXTENT_SERVICE_URL", "").rstrip("/")


def _headers() -> dict:
    # strip()：secret 值可能含尾端換行（建立時用 echo 而非 echo -n）；
    # header 值含 \n 會被 http.client 擋成 ValueError。一律去空白後送出。
    return {"X-API-Key": os.environ.get("SEARCH_EXTENT_API_KEY", "").strip(),
            "Content-Type": "application/json"}


def is_configured() -> bool:
    return bool(_base_url() and os.environ.get("SEARCH_EXTENT_API_KEY"))


def brand_presence(topic: str, brands: list, timeout: int = 180) -> dict:
    """品牌聲量探勘：主題 × 品牌清單 → 各品牌 earned 聲量等級。
    回 {status, topic, count, results:[...]} 或 {error}。"""
    base = _base_url()
    if not base:
        return {"error": "SEARCH_EXTENT_SERVICE_URL 未設定。"}
    try:
        resp = requests.post(f"{base}/api/brand-presence",
                             json={"topic": topic, "brands": brands},
                             headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "搜尋情報服務金鑰驗證失敗（401）。"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": f"品牌聲量探勘逾時（{timeout}s），請減少品牌數。"}
    except Exception as e:
        return {"error": f"無法連線至搜尋情報服務：{e}"}


def discover(query: str, max_results: int = 50, timeout: int = 180) -> dict:
    """關鍵字 → 推薦爬取 URL 清單。回 {status, candidates:[...], by_source, count} 或 {error}。"""
    base = _base_url()
    if not base:
        return {"error": "SEARCH_EXTENT_SERVICE_URL 未設定（搜尋情報服務未接上）。"}
    try:
        resp = requests.post(f"{base}/api/discover",
                             json={"query": query, "max": max_results},
                             headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            return {"error": "搜尋情報服務金鑰驗證失敗（401）。"}
        return resp.json()
    except requests.exceptions.Timeout:
        print(f"[search_extent] discover timeout base={base}", flush=True)
        return {"error": f"內容發現逾時（{timeout}s）。"}
    except Exception as e:
        import traceback
        print(f"[search_extent] discover EXC base={base} type={type(e).__name__} e={e!r}", flush=True)
        traceback.print_exc()
        return {"error": f"無法連線至搜尋情報服務：{type(e).__name__}: {e}"}
