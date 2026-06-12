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
import os
import uuid
import threading
import functools
import subprocess

import firebase_admin
from firebase_admin import firestore
from flask import Flask, request, jsonify

from crawler import HeadlessCrawler, UnsupportedSiteError
from auth import is_authorized
from crawl_job import run_crawl_batch, JOBS_COLLECTION

SERVICE_VERSION = "1.3.0"

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


def _run_scrape(url: str, use_gemini: bool, gemini_api_key: str,
                hard_timeout_sec: int = 60) -> dict:
    crawler = HeadlessCrawler()
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


# ── 同步端點（向後相容）──

@app.route("/api/scrape", methods=["POST"])
@require_api_key
def scrape():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"status": "failed", "error": "Missing 'url' in request body"}), 400
    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"status": "failed", "url": url, "error": "Invalid URL: must start with http(s)://"}), 400

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
        if not (url.startswith("http://") or url.startswith("https://")):
            results.append({"status": "failed", "url": url, "error": "Invalid URL"})
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

    use_gemini = bool(data.get("use_gemini", False))
    gemini_api_key = data.get("gemini_api_key") or os.environ.get("GENAI_API_KEY")

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
