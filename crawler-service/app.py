# -*- coding: utf-8 -*-
"""
獨立爬蟲服務 API 入口 (Cloud Run)

完全獨立、透過 API 操作的無頭瀏覽器爬蟲服務。
所有 /api/* 端點都必須帶上正確的 X-API-Key 才允許存取。
金鑰由 Cloud Run 以 Secret Manager 環境變數 CRAWLER_API_KEY 注入。

端點：
  GET  /health           健康檢查（不需金鑰，供 Cloud Run 探活）。
  POST /api/scrape       爬取單一網址（同步），需 X-API-Key。
  POST /api/scrape/batch 爬取多個網址（同步，依序執行），需 X-API-Key。

回傳格式（統一）：
  成功: {"status": "success", "url": "...", "title": "...", "content": "...", "length": N}
  略過: {"status": "skipped", "url": "...", "error": "..."}
  失敗: {"status": "failed",  "url": "...", "error": "..."}
"""
import os
import hmac
import functools
import subprocess

from flask import Flask, request, jsonify

from crawler import HeadlessCrawler, UnsupportedSiteError

SERVICE_VERSION = "1.2.0"

app = Flask(__name__)

# 由 Cloud Run 以 Secret Manager 注入；未設定時一律拒絕，確保預設安全。
CRAWLER_API_KEY = os.environ.get("CRAWLER_API_KEY")
if not CRAWLER_API_KEY:
    print("[WARNING] CRAWLER_API_KEY 未設定，所有 /api 請求都會被拒絕 (401)。", flush=True)


def _is_authorized(req) -> bool:
    """以常數時間比對 X-API-Key，避免 timing attack。"""
    if not CRAWLER_API_KEY:
        return False
    provided = req.headers.get("X-API-Key", "")
    return hmac.compare_digest(provided, CRAWLER_API_KEY)


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not _is_authorized(request):
            return jsonify({
                "status": "failed",
                "error": "Unauthorized: missing or invalid X-API-Key"
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
    }), 200


def _run_scrape(url: str, use_gemini: bool, gemini_api_key: str,
                hard_timeout_sec: int = 60) -> dict:
    """建立爬蟲實例、執行爬取、確保釋放資源，回傳標準化結果 dict。"""
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
    """批次爬取，依序執行，適合 Colab / 外部系統一次送多條 URL。

    請求格式:
      {"urls": ["https://...", "https://..."], "use_gemini": false, "gemini_api_key": "..."}

    回傳格式:
      {"results": [<result>, <result>, ...], "total": N, "succeeded": N, "failed": N}
    """
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

    return jsonify({
        "results": results,
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
