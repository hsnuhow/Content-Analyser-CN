# -*- coding: utf-8 -*-
"""
search-extent API 入口（Cloud Run）

需求側情報服務：種子關鍵字（來自分析的語意群 TF-IDF top 詞）→
Google Ads Keyword Planner 的關聯關鍵字 + 平均搜尋量 + 競爭度。
用於報告的「延伸附錄」與 §7 接地。

所有 /api/* 需 X-API-Key（系統 SEARCH_EXTENT_API_KEY 或 api_keys 白名單，需 'expand' 權限）。

端點：
  GET  /health       探活（不需金鑰）
  POST /api/expand   種子 → 關聯關鍵字（同步）
"""
import os
import functools

from flask import Flask, request, jsonify

import ads_client
from auth import is_authorized

SERVICE_VERSION = "0.1.0"

# Firebase（供 api_keys 白名單）；初始化失敗則只接受系統金鑰。
db = None
try:
    import firebase_admin
    from firebase_admin import firestore
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    db = firestore.client()
except Exception as e:
    print(f"[Firebase] 初始化失敗（僅系統金鑰可用）：{e}", flush=True)

app = Flask(__name__)

SEARCH_EXTENT_API_KEY = os.environ.get("SEARCH_EXTENT_API_KEY")
if not SEARCH_EXTENT_API_KEY:
    print("[WARNING] SEARCH_EXTENT_API_KEY 未設定，僅 api_keys 白名單可通過驗證。", flush=True)


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if not is_authorized(provided, SEARCH_EXTENT_API_KEY, "expand", db):
            return jsonify({"status": "failed",
                            "error": "Unauthorized: missing or invalid X-API-Key（需 'expand' 權限）"}), 401
        return f(*args, **kwargs)
    return wrapper


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "search-extent",
        "version": SERVICE_VERSION,
        "api_key_configured": bool(SEARCH_EXTENT_API_KEY),
        "ads_configured": ads_client.is_configured(),
        "firebase": "connected" if db is not None else "unavailable",
    }), 200


@app.route("/api/expand", methods=["POST"])
@require_api_key
def expand():
    """種子關鍵字 → 關聯關鍵字。

    Request body:
      {"seeds": ["初生光采","美白精華"], "language_id": "1018",
       "geo_ids": ["2158"], "limit": 200}
    Response:
      {"status":"ok","seeds":[...],"count":N,"ideas":[{text,avg_monthly_searches,competition,...}]}
    """
    data = request.get_json(silent=True) or {}
    seeds = data.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        return jsonify({"status": "failed", "error": "缺少 seeds（非空字串陣列）"}), 400
    seeds = [str(s).strip() for s in seeds if str(s).strip()]
    if not seeds:
        return jsonify({"status": "failed", "error": "seeds 全為空"}), 400

    language_id = str(data.get("language_id")).strip() if data.get("language_id") else None
    geo_ids = data.get("geo_ids")
    if isinstance(geo_ids, list):
        geo_ids = [str(g).strip() for g in geo_ids if str(g).strip()]
    else:
        geo_ids = None
    try:
        limit = max(1, min(1000, int(data.get("limit", 200))))
    except (TypeError, ValueError):
        limit = 200

    try:
        ideas = ads_client.generate_keyword_ideas(
            seeds, language_id=language_id, geo_ids=geo_ids, limit=limit)
    except ads_client.AdsConfigError as e:
        return jsonify({"status": "failed", "error": f"Ads 設定錯誤：{e}"}), 503
    except ValueError as e:
        return jsonify({"status": "failed", "error": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Ads API 呼叫失敗：{e}"}), 502

    return jsonify({
        "status": "ok",
        "seeds": seeds,
        "count": len(ideas),
        "ideas": ideas,
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
