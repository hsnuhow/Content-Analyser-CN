# -*- coding: utf-8 -*-
"""
獨立爬蟲服務 API 入口 (Cloud Run)

這是一個完全獨立、透過 API 操作的無頭瀏覽器爬蟲服務。
所有 /api/* 端點都必須帶上正確的 X-API-Key 才允許存取（金鑰存於 Secret Manager，
由 Cloud Run 以環境變數 CRAWLER_API_KEY 注入）。

端點：
  GET  /health        健康檢查（不需金鑰，供 Cloud Run 探活）。
  POST /api/scrape    爬取單一網址（同步），需 X-API-Key。
"""
import os
import hmac
import functools

from flask import Flask, request, jsonify

from crawler import HeadlessCrawler

app = Flask(__name__)

# 由 Cloud Run 以 Secret Manager 注入；未設定時一律拒絕，確保預設安全。
CRAWLER_API_KEY = os.environ.get("CRAWLER_API_KEY")
if not CRAWLER_API_KEY:
    print("[WARNING] 環境變數 CRAWLER_API_KEY 未設定，所有 /api 請求都會被拒絕 (401)。", flush=True)


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
                "status": "error",
                "error": "Unauthorized: missing or invalid X-API-Key"
            }), 401
        return f(*args, **kwargs)
    return wrapper


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "content-crawler"}), 200


@app.route("/api/scrape", methods=["POST"])
@require_api_key
def scrape():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"status": "error", "error": "Missing 'url' in request body"}), 400

    # 基本網址格式驗證
    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"status": "error", "error": "Invalid URL: must start with http:// or https://"}), 400

    use_gemini = bool(data.get("use_gemini", False))
    # 優先使用呼叫端傳入的金鑰，否則回退服務本身的預設金鑰
    gemini_api_key = data.get("gemini_api_key") or os.environ.get("GENAI_API_KEY")

    crawler = HeadlessCrawler()
    try:
        if use_gemini and gemini_api_key:
            crawler.configure_genai(gemini_api_key)
        result = crawler.scrape(url)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"status": "failed", "url": url, "error": str(e)}), 500
    finally:
        crawler.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
