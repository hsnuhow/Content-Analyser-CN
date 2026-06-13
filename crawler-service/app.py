# -*- coding: utf-8 -*-
"""
獨立爬蟲服務 API 入口 (Cloud Run)

完全獨立、透過 API 操作的無頭瀏覽器爬蟲服務。
所有 /api/* 端點需 X-API-Key（系統金鑰或 api_keys 白名單，需 'crawl' 權限）。

端點：
  GET  /health              健康檢查（不需金鑰）。
  POST /api/scrape          爬取單一網址（同步）。
  POST /api/scrape/batch    爬取多網址（同步，最多 20）。
  POST /api/crawl/batch     爬取多網址（非同步），回傳 job_id。
  GET  /api/crawl/{job_id}  查詢非同步爬取進度與結果。

回傳格式（單篇）：
  成功: {"status": "success", "url", "title", "content", "length"}
  略過: {"status": "skipped", "url", "error"}
  失敗: {"status": "failed",  "url", "error"}
"""
import ipaddress
import os
import uuid
import threading
import functools
import subprocess
from urllib.parse import urlparse

import firebase_admin
from firebase_admin import firestore
from flask import Flask, request, jsonify

from crawler import HeadlessCrawler, UnsupportedSiteError
from auth import is_authorized
from crawl_job import run_crawl_batch, JOBS_COLLECTION

SERVICE_VERSION = "1.4.0"


def _is_safe_url(url: str):
    """C1 SSRF 防護：阻擋私有/保留 IP、loopback、link-local（含 GCP metadata）。
    回傳 (ok: bool, reason: str)。
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"非 http/https 協議：{parsed.scheme}"
        host = parsed.hostname or ""
        if not host:
            return False, "缺少 hostname"
        # 已知危險 hostname
        _BLOCKED_HOSTS = {"metadata.google.internal", "169.254.169.254"}
        if host.lower() in _BLOCKED_HOSTS:
            return False, f"禁止存取 metadata endpoint：{host}"
        # 若 hostname 是 IP，直接檢查範圍
        try:
            ip = ipaddress.ip_address(host)
            if (ip.is_private or ip.is_loopback or
                    ip.is_link_local or ip.is_reserved or ip.is_multicast):
                return False, f"禁止存取保留/私有 IP：{host}"
        except ValueError:
            pass  # 是 domain name，信任 DNS（避免 TOCTOU 的 DNS resolution）
        return True, ""
    except Exception as e:
        return False, str(e)

# ── Firebase 初始化（供非同步 job 與 api_keys 驗證）──
db = None
if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app()
        print("[Firebase] Initialized with ADC.", flush=True)
    except Exception as e:
        print(f"[Firebase] Init failed: {e}", flush=True)
try:
    db = firestore.client()
except Exception as e:
    print(f"[Firebase] firestore client 取得失敗: {e}", flush=True)

app = Flask(__name__)

CRAWLER_API_KEY = os.environ.get("CRAWLER_API_KEY")
if not CRAWLER_API_KEY:
    print("[WARNING] CRAWLER_API_KEY 未設定，僅 api_keys 白名單可通過驗證。", flush=True)


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if not is_authorized(provided, CRAWLER_API_KEY, "crawl", db):
            return jsonify({
                "status": "failed",
                "error": "Unauthorized: missing or invalid X-API-Key（需 'crawl' 權限）"
            }), 401
        return f(*args, **kwargs)
    return wrapper


def _get_chrome_version():
    try:
        out = subprocess.check_output(
            ["google-chrome", "--version"], stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
        return out
    except Exception:
        return "unavailable"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "content-crawler",
        "version": SERVICE_VERSION,
        "chrome": _get_chrome_version(),
        "api_key_configured": bool(CRAWLER_API_KEY),
        "firebase": "connected" if db is not None else "unavailable",
    }), 200


def _tier1_scrape(url: str, use_gemini: bool, gemini_api_key: str,
                  hard_timeout_sec: int, use_proxy: bool = False) -> dict:
    """Tier 1：undetected-chromedriver 爬取（use_proxy=True 時走 Tier 3 代理）。"""
    crawler = HeadlessCrawler(use_proxy=use_proxy)
    try:
        if use_gemini and gemini_api_key:
            crawler.configure_genai(gemini_api_key)
        return crawler.scrape(url, hard_timeout_sec=hard_timeout_sec)
    except UnsupportedSiteError as e:
        return {"status": "skipped", "url": url, "error": str(e)}
    except Exception as e:
        return {"status": "failed", "url": url, "error": str(e)}
    finally:
        crawler.close()


# 視為「需要升級」的條件：失敗，或成功但內文過短（疑似只抓到導語）
_TIER_UPGRADE_MIN_LEN = 200


def _needs_upgrade(result: dict) -> bool:
    if result.get("status") == "skipped":
        return False  # skip（需登入等）升級也沒用
    if result.get("status") != "success":
        return True
    return len((result.get("content") or "")) < _TIER_UPGRADE_MIN_LEN


def _run_scrape(url: str, use_gemini: bool, gemini_api_key: str,
                hard_timeout_sec: int = 60) -> dict:
    """分層爬取協調器（Tier 1 → 2 → 3）。

    Tier 2/3 皆由環境變數控制、預設關閉：未設定時行為與單純 Tier 1 完全相同。
    見 tiered_fallback.py 與 CRAWLER_STRATEGY.md。
    """
    # ── Tier 1：無頭瀏覽器（直連）──
    result = _tier1_scrape(url, use_gemini, gemini_api_key, hard_timeout_sec)
    if not _needs_upgrade(result):
        return result

    # ── Tier 2：Gemini URL 直讀（env: ENABLE_GEMINI_URL_FALLBACK）──
    try:
        from tiered_fallback import is_gemini_url_fallback_enabled, gemini_url_read
        if is_gemini_url_fallback_enabled():
            key = gemini_api_key or os.environ.get("GENAI_API_KEY")
            text = gemini_url_read(url, key, log_fn=lambda m: print(m, flush=True))
            if len(text) >= _TIER_UPGRADE_MIN_LEN:
                return {"status": "success", "url": url,
                        "title": result.get("title") or "(Tier2 Gemini)",
                        "content": text, "length": len(text), "tier": 2}
    except Exception as e:
        print(f"[Tier2] 協調失敗：{e}", flush=True)

    # ── Tier 3：Webshare 住宅 IP 代理重試（env: WEBSHARE_PROXY_ENABLED）──
    try:
        from tiered_fallback import load_proxy_config
        if load_proxy_config() is not None:
            print(f"[Tier3] Tier1/2 失敗，改用 Webshare 代理重試：{url}", flush=True)
            proxied = _tier1_scrape(url, use_gemini, gemini_api_key,
                                    hard_timeout_sec, use_proxy=True)
            if not _needs_upgrade(proxied):
                proxied["tier"] = 3
                return proxied
    except Exception as e:
        print(f"[Tier3] 協調失敗：{e}", flush=True)

    # 全部失敗：回傳 Tier 1 的結果（保留原始錯誤訊息）
    return result


# ── 同步端點（向後相容）──

@app.route("/api/scrape", methods=["POST"])
@require_api_key
def scrape():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"status": "failed", "error": "Missing 'url' in request body"}), 400
    safe, reason = _is_safe_url(url)
    if not safe:
        return jsonify({"status": "failed", "url": url, "error": f"URL 不合法：{reason}"}), 400

    use_gemini = bool(data.get("use_gemini", False))
    gemini_api_key = data.get("gemini_api_key") or os.environ.get("GENAI_API_KEY")
    hard_timeout_sec = int(data.get("hard_timeout_sec", 60))

    result = _run_scrape(url, use_gemini, gemini_api_key, hard_timeout_sec)
    http_status = 200 if result.get("status") in ("success", "skipped") else 500
    return jsonify(result), http_status


@app.route("/api/scrape/batch", methods=["POST"])
@require_api_key
def scrape_batch():
    """同步批次爬取（最多 20，依序執行）。適合少量、可等待的場景。"""
    data = request.get_json(silent=True) or {}
    urls = data.get("urls") or []

    if not isinstance(urls, list) or not urls:
        return jsonify({"status": "failed", "error": "Missing or empty 'urls' list"}), 400
    if len(urls) > 20:
        return jsonify({"status": "failed", "error": "Maximum 20 URLs per batch request"}), 400

    use_gemini = bool(data.get("use_gemini", False))
    gemini_api_key = data.get("gemini_api_key") or os.environ.get("GENAI_API_KEY")

    results = []
    for url in urls:
        url = (url or "").strip()
        if not url:
            results.append({"status": "failed", "url": url, "error": "Empty URL"})
            continue
        safe, reason = _is_safe_url(url)
        if not safe:
            results.append({"status": "failed", "url": url, "error": f"URL 不合法：{reason}"})
            continue
        results.append(_run_scrape(url, use_gemini, gemini_api_key))

    succeeded = sum(1 for r in results if r.get("status") == "success")
    failed = sum(1 for r in results if r.get("status") == "failed")
    return jsonify({"results": results, "total": len(results),
                    "succeeded": succeeded, "failed": failed}), 200


# ── 非同步端點（建議用於 UI / Colab 大批量）──

@app.route("/api/crawl/batch", methods=["POST"])
@require_api_key
def crawl_batch():
    """非同步批次爬取，回傳 job_id。背景逐一爬取，結果存 Firestore crawl_jobs。

    Request: {"urls": [...], "use_gemini": false, "gemini_api_key": "..."}
    Response: {"job_id": "...", "status": "pending"}
    """
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線，無法建立非同步任務"}), 503

    data = request.get_json(silent=True) or {}
    urls = data.get("urls") or []
    if not isinstance(urls, list) or not urls:
        return jsonify({"status": "failed", "error": "Missing or empty 'urls' list"}), 400
    if len(urls) > 100:
        return jsonify({"status": "failed", "error": "Maximum 100 URLs per crawl job"}), 400
    # C1: 先過濾掉不安全 URL，回報給呼叫端
    safe_urls, blocked = [], []
    for u in urls:
        ok, reason = _is_safe_url((u or "").strip())
        if ok:
            safe_urls.append(u)
        else:
            blocked.append({"url": u, "reason": reason})
    if blocked:
        return jsonify({
            "status": "failed",
            "error": f"{len(blocked)} 個 URL 被 SSRF 過濾拒絕",
            "blocked": blocked,
        }), 400

    use_gemini = bool(data.get("use_gemini", False))
    gemini_api_key = data.get("gemini_api_key") or os.environ.get("GENAI_API_KEY")
    urls = safe_urls

    job_id = str(uuid.uuid4())
    db.collection(JOBS_COLLECTION).document(job_id).set({
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "log": "任務已建立，等待執行...",
        "total": len(urls),
        "results": [],
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "completed_at": None,
    })

    t = threading.Thread(
        target=run_crawl_batch,
        args=(job_id, urls, use_gemini, gemini_api_key, db),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id, "status": "pending"}), 202


@app.route("/api/crawl/<job_id>", methods=["GET"])
@require_api_key
def get_crawl_job(job_id):
    """查詢非同步爬取任務進度與結果。"""
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    try:
        doc = db.collection(JOBS_COLLECTION).document(job_id).get()
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Firestore 查詢失敗: {e}"}), 500
    if not doc.exists:
        return jsonify({"status": "failed", "error": f"找不到 job_id: {job_id}"}), 404
    return jsonify(doc.to_dict()), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
