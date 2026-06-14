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

SERVICE_VERSION = "1.5.0"


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


def _run_scrape(url: str, use_gemini: bool, gemini_api_key: str,
                hard_timeout_sec: int = 60) -> dict:
    """分層爬取協調器（Tier 1 → 2 → 3）。

    Tier 2/3 皆由環境變數控制、預設關閉：未設定時行為與單純 Tier 1 完全相同。
    見 tiered_fallback.py 與 CRAWLER_STRATEGY.md。
    """
    # ── Tier 1：無頭瀏覽器（直連）──
    result = _tier1_scrape(url, use_gemini, gemini_api_key, hard_timeout_sec)

    # ── Tier 2/3：交給共用協調器（Tier 3 代理重試用獨立 crawler）──
    try:
        from tiered_fallback import run_tier23
        key = gemini_api_key or os.environ.get("GENAI_API_KEY")
        return run_tier23(
            url, result, key,
            proxied_scrape_fn=lambda u: _tier1_scrape(
                u, use_gemini, gemini_api_key, hard_timeout_sec, use_proxy=True),
            log_fn=lambda m: print(m, flush=True),
        )
    except Exception as e:
        print(f"[Tier2/3] 協調失敗（回退 Tier1 結果）：{e}", flush=True)
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
    if len(urls) > 1000:
        return jsonify({"status": "failed", "error": "Maximum 1000 URLs per crawl job"}), 400
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
    data = doc.to_dict()
    # 結果存於 results 子集合（避免單文件 1MB 上限）；組裝回傳。後備：舊格式內嵌 results。
    try:
        sub = (db.collection(JOBS_COLLECTION).document(job_id)
               .collection("results").order_by("__name__").stream())
        results = [r.to_dict() for r in sub]
        if results:
            data["results"] = results
        else:
            data.setdefault("results", [])
    except Exception as e:
        print(f"[Crawler] 讀取 results 子集合失敗: {e}", flush=True)
        data.setdefault("results", [])
    return jsonify(data), 200


@app.route("/api/crawl/<job_id>/cancel", methods=["POST"])
@require_api_key
def cancel_crawl_job(job_id):
    """請求取消非同步爬取任務（合作式）。

    設 cancel_requested=True；背景迴圈於每篇前檢查，收到即停止並轉 cancelled。
    若任務已完成/失敗則不影響。回傳當前狀態。
    """
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    try:
        ref = db.collection(JOBS_COLLECTION).document(job_id)
        doc = ref.get()
        if not doc.exists:
            return jsonify({"status": "failed", "error": f"找不到 job_id: {job_id}"}), 404
        cur = doc.to_dict().get("status")
        if cur in ("completed", "failed", "cancelled"):
            return jsonify({"status": cur, "message": "任務已結束，無需取消"}), 200
        ref.update({"cancel_requested": True,
                    "updated_at": firestore.SERVER_TIMESTAMP})
        return jsonify({"status": "cancelling", "job_id": job_id}), 200
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e)}), 500


@app.route("/api/crawl/cleanup", methods=["POST"])
@require_api_key
def cleanup_crawl_jobs():
    """清除孤兒/陳舊爬取任務文件（status 已結束且超過 days 天）。

    Request: {"days": 7}（預設 7）。回傳刪除筆數。
    crawl_jobs 是 crawler 的暫存層，結果回收進 content-analyser 後即可清理。
    """
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    import datetime
    data = request.get_json(silent=True) or {}
    try:
        days = max(0, int(data.get("days", 7)))
    except (TypeError, ValueError):
        days = 7
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    deleted = 0
    try:
        for doc in db.collection(JOBS_COLLECTION).stream():
            d = doc.to_dict() or {}
            if d.get("status") not in ("completed", "failed", "cancelled"):
                continue
            updated = d.get("updated_at") or d.get("completed_at")
            # 無時間戳或早於 cutoff → 刪除（含 results 子集合）
            if updated is None or updated < cutoff:
                try:
                    for r in doc.reference.collection("results").stream():
                        r.reference.delete()
                except Exception:
                    pass
                doc.reference.delete()
                deleted += 1
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e), "deleted": deleted}), 500
    return jsonify({"status": "ok", "deleted": deleted, "days": days}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
