# -*- coding: utf-8 -*-
"""
Analysis Pipeline API 入口 (Cloud Run)

獨立的內容分析服務。所有 /api/* 端點需 X-API-Key。

端點：
  GET  /health                   健康檢查（無需金鑰）
  POST /api/analyse              提交分析任務（非同步），回傳 job_id
  GET  /api/analyse/{job_id}     查詢任務進度與結果

分析流程（background thread）：
  Path 1（TF-IDF + Vertex AI 分群）
  Path 2（LLM 搜尋意圖 + 質化分析）  ← 平行執行
  Synthesis LLM → 最終 Markdown 報告
"""
import os
import hmac
import uuid
import threading
import functools

import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, request, jsonify

from pipeline import run_analysis, JOBS_COLLECTION

SERVICE_VERSION = "1.0.0"

# ── Firebase 初始化 ──
if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app()
        print("[Firebase] Initialized with ADC.", flush=True)
    except Exception as e:
        print(f"[Firebase] Init failed: {e}", flush=True)

db = firestore.client()

# ── Flask ──
app = Flask(__name__)

ANALYSIS_API_KEY = os.environ.get("ANALYSIS_API_KEY")
if not ANALYSIS_API_KEY:
    print("[WARNING] ANALYSIS_API_KEY 未設定，所有 /api 請求將被拒絕 (401)。", flush=True)


def _is_authorized(req) -> bool:
    if not ANALYSIS_API_KEY:
        return False
    provided = req.headers.get("X-API-Key", "")
    return hmac.compare_digest(provided, ANALYSIS_API_KEY)


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not _is_authorized(request):
            return jsonify({"status": "failed",
                            "error": "Unauthorized: missing or invalid X-API-Key"}), 401
        return f(*args, **kwargs)
    return wrapper


# ──────────────────────────────────────────────────────────────────────
# 端點
# ──────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "analysis-pipeline",
        "version": SERVICE_VERSION,
        "api_key_configured": bool(ANALYSIS_API_KEY),
        "firebase": "connected" if firebase_admin._apps else "unavailable",
    }), 200


@app.route("/api/analyse", methods=["POST"])
@require_api_key
def analyse():
    """提交分析任務（非同步）。

    Request body:
    {
      "report_title": "CHANEL 初生光采 × 美白透亮",
      "contents": [
        {"url": "...", "title": "...", "text": "...", "source_type": "media"},
        ...
      ],
      "llm_provider":  "gemini" | "claude",
      "llm_model":     "gemini-2.0-flash" | "claude-sonnet-4-5" | ...,
      "llm_api_key":   "AIza..."
    }

    Response:
    {"job_id": "abc123", "status": "pending"}
    """
    data = request.get_json(silent=True) or {}

    # ── 驗證必填欄位 ──
    report_title = (data.get("report_title") or "").strip()
    if not report_title:
        return jsonify({"status": "failed", "error": "缺少 report_title"}), 400

    contents = data.get("contents")
    if not isinstance(contents, list) or not contents:
        return jsonify({"status": "failed", "error": "缺少 contents 或為空列表"}), 400

    if len(contents) > 100:
        return jsonify({"status": "failed",
                        "error": "每次分析最多 100 篇內容"}), 400

    llm_provider = (data.get("llm_provider") or "gemini").strip().lower()
    llm_model = (data.get("llm_model") or "gemini-2.0-flash").strip()
    llm_api_key = (data.get("llm_api_key") or "").strip()

    if not llm_api_key:
        return jsonify({"status": "failed",
                        "error": "缺少 llm_api_key。請在 Project 設定中配置 LLM API Key。"}), 400

    if llm_provider not in ("gemini", "claude"):
        return jsonify({"status": "failed",
                        "error": f"不支援的 llm_provider：'{llm_provider}'。請使用 'gemini' 或 'claude'。"}), 400

    # ── 建立 Firestore job 文件 ──
    job_id = str(uuid.uuid4())
    job_ref = db.collection(JOBS_COLLECTION).document(job_id)
    job_ref.set({
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "log": "任務已建立，等待執行...",
        "report_title": report_title,
        "n_articles": len(contents),
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "result_markdown": None,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "completed_at": None,
    })

    # ── 背景 thread 執行分析 ──
    llm_config = {
        "provider": llm_provider,
        "model": llm_model,
        "api_key": llm_api_key,
    }
    t = threading.Thread(
        target=run_analysis,
        args=(job_id, report_title, contents, llm_config, db),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id, "status": "pending"}), 202


@app.route("/api/analyse/<job_id>", methods=["GET"])
@require_api_key
def get_job(job_id: str):
    """查詢分析任務進度與結果。

    Response:
      進行中: {"job_id", "status": "running", "progress": 45, "log": "..."}
      完成:   {"job_id", "status": "completed", "progress": 100, "result_markdown": "..."}
      失敗:   {"job_id", "status": "failed", "log": "錯誤說明"}
    """
    try:
        doc = db.collection(JOBS_COLLECTION).document(job_id).get()
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Firestore 查詢失敗：{e}"}), 500

    if not doc.exists:
        return jsonify({"status": "failed", "error": f"找不到 job_id：{job_id}"}), 404

    job = doc.to_dict()
    # 只回傳前端需要的欄位（避免傳回 llm_api_key）
    safe_fields = {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "log": job.get("log", ""),
        "report_title": job.get("report_title"),
        "n_articles": job.get("n_articles"),
        "llm_provider": job.get("llm_provider"),
        "llm_model": job.get("llm_model"),
    }
    if job.get("status") == "completed":
        safe_fields["result_markdown"] = job.get("result_markdown", "")
    if job.get("status") == "failed":
        safe_fields["error"] = job.get("log", "")

    return jsonify(safe_fields), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
