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
    return {"X-API-Key": os.environ.get("SEARCH_EXTENT_API_KEY", ""),
            "Content-Type": "application/json"}


def is_configured() -> bool:
    return bool(_base_url() and os.environ.get("SEARCH_EXTENT_API_KEY"))


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


def diag() -> dict:
    """臨時診斷：從本服務環境實打 search-extent，回原始結果/例外（含 /health 與一次小 discover）。"""
    import traceback
    out = {"base": _base_url(), "key_set": bool(os.environ.get("SEARCH_EXTENT_API_KEY"))}
    base = _base_url()
    if not base:
        out["error"] = "SEARCH_EXTENT_SERVICE_URL 未設定"
        return out
    # 1) /health（無金鑰）
    try:
        h = requests.get(f"{base}/health", timeout=20)
        out["health_status"] = h.status_code
        out["health_body"] = h.text[:200]
    except Exception as e:
        out["health_exc"] = f"{type(e).__name__}: {e}"
    # 2) /api/discover（帶金鑰，小查詢）
    try:
        r = requests.post(f"{base}/api/discover", json={"query": "循環扇", "max": 3},
                          headers=_headers(), timeout=180)
        out["discover_status"] = r.status_code
        out["discover_body"] = r.text[:400]
    except Exception as e:
        out["discover_exc"] = f"{type(e).__name__}: {e}"
        out["discover_tb"] = traceback.format_exc()[-600:]
    return out
