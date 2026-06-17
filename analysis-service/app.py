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
import uuid
import threading
import functools

import firebase_admin
from firebase_admin import firestore
from flask import Flask, request, jsonify

from pipeline import run_analysis, JOBS_COLLECTION
from image_report import run_image_analysis, JOBS_COLLECTION as IMAGE_JOBS_COLLECTION
from combined_report import run_combined_report, JOBS_COLLECTION as COMBINED_JOBS_COLLECTION
from auth import is_authorized

SERVICE_VERSION = "1.2.0"

_REAP_COLLECTIONS = [JOBS_COLLECTION, IMAGE_JOBS_COLLECTION, COMBINED_JOBS_COLLECTION]

# ── Firebase 初始化 ──
if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app()
        print("[Firebase] Initialized with ADC.", flush=True)
    except Exception as e:
        print(f"[Firebase] Init failed: {e}", flush=True)

db = firestore.client()


def _reap():
    """收割本服務 3 個集合的卡住任務（reap-on-submit / cleanup 觸發，全自動、零外部排程）。"""
    try:
        from reaper import reap_stale
        return reap_stale(db, _REAP_COLLECTIONS)
    except Exception as e:
        print(f"[Reaper] 觸發失敗（略過）: {e}", flush=True)
        return 0

# ── Flask ──
app = Flask(__name__)

ANALYSIS_API_KEY = os.environ.get("ANALYSIS_API_KEY")
if not ANALYSIS_API_KEY:
    print("[WARNING] ANALYSIS_API_KEY 未設定，僅 api_keys 白名單可通過驗證。", flush=True)


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if not is_authorized(provided, ANALYSIS_API_KEY, "analyse", db):
            return jsonify({"status": "failed",
                            "error": "Unauthorized: missing or invalid X-API-Key（需 'analyse' 權限）"}), 401
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
      "llm_model":     "gemini-2.5-flash" | "claude-sonnet-4-5" | ...,
      "llm_api_key":   "AIza..."
    }

    Response:
    {"job_id": "abc123", "status": "pending"}
    """
    _reap()  # reap-on-submit：收割卡住任務（全自動）
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

    total_text_len = sum(len(str(c.get("text") or c.get("content") or "")) for c in contents)
    if total_text_len > 5_000_000:
        return jsonify({"status": "failed",
                        "error": f"內容總長度過大（{total_text_len:,} 字元），上限 5,000,000 字元"}), 400

    llm_provider = (data.get("llm_provider") or "gemini").strip().lower()
    llm_model = (data.get("llm_model") or "gemini-2.5-flash").strip()
    llm_api_key = (data.get("llm_api_key") or "").strip()
    try:
        temperature = max(0.0, min(1.0, float(data.get("temperature", 0.3))))
    except (TypeError, ValueError):
        temperature = 0.3
    thinking = bool(data.get("thinking", False))
    search_extent = bool(data.get("search_extent", True))  # 預設開（可關）

    # 進階參數：輸出長度上限(A)、top_p、輸入內容量(B)
    try:
        max_output_tokens = int(data.get("max_output_tokens") or 8192)
        max_output_tokens = max(256, min(32768, max_output_tokens))
    except (TypeError, ValueError):
        max_output_tokens = 8192
    top_p = data.get("top_p")
    if top_p is not None:
        try:
            top_p = max(0.0, min(1.0, float(top_p)))
        except (TypeError, ValueError):
            top_p = None
    input_scale = str(data.get("input_scale", "standard")).strip().lower()
    if input_scale not in ("standard", "large", "max"):
        input_scale = "standard"

    if not llm_api_key:
        return jsonify({"status": "failed",
                        "error": "缺少 llm_api_key。請在 Project 設定中配置 LLM API Key。"}), 400

    if llm_provider not in ("gemini", "claude", "openai"):
        return jsonify({"status": "failed",
                        "error": f"不支援的 llm_provider：'{llm_provider}'。請使用 'gemini'、'claude' 或 'openai'。"}), 400

    if not llm_model:
        return jsonify({"status": "failed", "error": "缺少 llm_model。"}), 400

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
        "temperature": temperature,
        "thinking": thinking,
        "search_extent": search_extent,
        "max_output_tokens": max_output_tokens,
        "top_p": top_p,
        "input_scale": input_scale,
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
        safe_fields["numeric_exports"] = job.get("numeric_exports", {})
    if job.get("status") == "failed":
        safe_fields["error"] = job.get("log", "")

    return jsonify(safe_fields), 200


@app.route("/api/analyse-images", methods=["POST"])
@require_api_key
def analyse_images():
    """提交影像視覺分析（非同步，階段②）。對主文大圖做色盤 + Gemini 視覺分析 → 報告。

    Request body:
    {
      "report_title": "...",
      "images": [{"src": "...", "alt": "...", "source_url": "文章URL（破解防盜連）"}],
      "llm_provider": "gemini", "llm_model": "gemini-2.5-flash", "llm_api_key": "..."
    }
    Response: {"job_id": "...", "status": "pending"}
    """
    _reap()  # reap-on-submit
    data = request.get_json(silent=True) or {}
    report_title = (data.get("report_title") or "").strip()
    if not report_title:
        return jsonify({"status": "failed", "error": "缺少 report_title"}), 400
    images = data.get("images")
    if not isinstance(images, list) or not images:
        return jsonify({"status": "failed", "error": "缺少 images 或為空列表"}), 400
    if len(images) > 200:
        return jsonify({"status": "failed", "error": "每次最多 200 張圖"}), 400

    llm_provider = (data.get("llm_provider") or "gemini").strip().lower()
    llm_model = (data.get("llm_model") or "gemini-2.5-flash").strip()
    llm_api_key = (data.get("llm_api_key") or "").strip()
    try:
        temperature = max(0.0, min(1.0, float(data.get("temperature", 0.3))))
    except (TypeError, ValueError):
        temperature = 0.3
    if not llm_api_key:
        return jsonify({"status": "failed",
                        "error": "缺少 llm_api_key。請在 Project 設定中配置 LLM API Key。"}), 400
    if llm_provider not in ("gemini", "claude"):
        return jsonify({"status": "failed",
                        "error": f"影像分析僅支援 'gemini'、'claude'（建議 gemini），不支援：'{llm_provider}'。"}), 400
    if not llm_model:
        return jsonify({"status": "failed", "error": "缺少 llm_model。"}), 400

    job_id = str(uuid.uuid4())
    db.collection(IMAGE_JOBS_COLLECTION).document(job_id).set({
        "job_id": job_id, "status": "pending", "progress": 0,
        "log": "任務已建立，等待執行...", "report_title": report_title,
        "n_images": len(images), "llm_provider": llm_provider, "llm_model": llm_model,
        "result_markdown": None,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP, "completed_at": None,
    })
    llm_cfg = {"provider": llm_provider, "model": llm_model,
               "api_key": llm_api_key, "temperature": temperature,
               "thinking": bool(data.get("thinking", False))}
    threading.Thread(target=run_image_analysis,
                     args=(job_id, report_title, images, llm_cfg, db),
                     daemon=True).start()
    return jsonify({"job_id": job_id, "status": "pending"}), 202


@app.route("/api/analyse-images/<job_id>", methods=["GET"])
@require_api_key
def get_image_job(job_id: str):
    """查詢影像視覺分析任務進度與結果（不回傳 llm_api_key）。"""
    try:
        doc = db.collection(IMAGE_JOBS_COLLECTION).document(job_id).get()
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Firestore 查詢失敗：{e}"}), 500
    if not doc.exists:
        return jsonify({"status": "failed", "error": f"找不到 job_id：{job_id}"}), 404
    job = doc.to_dict()
    safe = {
        "job_id": job.get("job_id"), "status": job.get("status"),
        "progress": job.get("progress", 0), "log": job.get("log", ""),
        "report_title": job.get("report_title"), "n_images": job.get("n_images"),
        "n_success": job.get("n_success"),
        "llm_provider": job.get("llm_provider"), "llm_model": job.get("llm_model"),
    }
    if job.get("status") == "completed":
        safe["result_markdown"] = job.get("result_markdown", "")
    if job.get("status") == "failed":
        safe["error"] = job.get("log", "")
    return jsonify(safe), 200


@app.route("/api/synthesize-combined", methods=["POST"])
@require_api_key
def synthesize_combined():
    """提交整合報告（非同步，階段③）：文字報告 × 視覺報告 → 整合策略報告。

    Request: {report_title, text_markdown, visual_markdown, topic?,
              llm_provider, llm_model, llm_api_key}
    Response: {"job_id": "...", "status": "pending"}
    """
    _reap()  # reap-on-submit
    data = request.get_json(silent=True) or {}
    report_title = (data.get("report_title") or "").strip()
    text_md = data.get("text_markdown") or ""
    visual_md = data.get("visual_markdown") or ""
    if not report_title:
        return jsonify({"status": "failed", "error": "缺少 report_title"}), 400
    if not text_md.strip() or not visual_md.strip():
        return jsonify({"status": "failed",
                        "error": "需同時提供 text_markdown 與 visual_markdown"}), 400
    topic = (data.get("topic") or report_title).strip()
    llm_provider = (data.get("llm_provider") or "gemini").strip().lower()
    llm_model = (data.get("llm_model") or "gemini-2.5-flash").strip()
    llm_api_key = (data.get("llm_api_key") or "").strip()
    try:
        temperature = max(0.0, min(1.0, float(data.get("temperature", 0.3))))
    except (TypeError, ValueError):
        temperature = 0.3
    if not llm_api_key:
        return jsonify({"status": "failed",
                        "error": "缺少 llm_api_key。請在 Project 設定中配置 LLM API Key。"}), 400
    if llm_provider not in ("gemini", "claude", "openai"):
        return jsonify({"status": "failed",
                        "error": f"不支援的 llm_provider：'{llm_provider}'。"}), 400

    job_id = str(uuid.uuid4())
    db.collection(COMBINED_JOBS_COLLECTION).document(job_id).set({
        "job_id": job_id, "status": "pending", "progress": 0,
        "log": "任務已建立，等待整合...", "report_title": report_title,
        "llm_provider": llm_provider, "llm_model": llm_model,
        "result_markdown": None,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP, "completed_at": None,
    })
    llm_cfg = {"provider": llm_provider, "model": llm_model,
               "api_key": llm_api_key, "temperature": temperature,
               "thinking": bool(data.get("thinking", False))}
    threading.Thread(target=run_combined_report,
                     args=(job_id, report_title, text_md, visual_md, topic, llm_cfg, db),
                     daemon=True).start()
    return jsonify({"job_id": job_id, "status": "pending"}), 202


@app.route("/api/synthesize-combined/<job_id>", methods=["GET"])
@require_api_key
def get_combined_job(job_id: str):
    """查詢整合報告任務進度與結果（不回傳 llm_api_key）。"""
    try:
        doc = db.collection(COMBINED_JOBS_COLLECTION).document(job_id).get()
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Firestore 查詢失敗：{e}"}), 500
    if not doc.exists:
        return jsonify({"status": "failed", "error": f"找不到 job_id：{job_id}"}), 404
    job = doc.to_dict()
    safe = {
        "job_id": job.get("job_id"), "status": job.get("status"),
        "progress": job.get("progress", 0), "log": job.get("log", ""),
        "report_title": job.get("report_title"),
        "llm_provider": job.get("llm_provider"), "llm_model": job.get("llm_model"),
    }
    if job.get("status") == "completed":
        safe["result_markdown"] = job.get("result_markdown", "")
    if job.get("status") == "failed":
        safe["error"] = job.get("log", "")
    return jsonify(safe), 200


@app.route("/api/analyse/<job_id>/cancel", methods=["POST"])
@require_api_key
def cancel_job(job_id: str):
    """請求取消分析任務（合作式）。

    設 cancel_requested=True；pipeline 於各檢查點檢查，收到即停止並轉 cancelled。
    已完成/失敗則不影響。
    """
    try:
        ref = db.collection(JOBS_COLLECTION).document(job_id)
        doc = ref.get()
        if not doc.exists:
            return jsonify({"status": "failed", "error": f"找不到 job_id：{job_id}"}), 404
        cur = doc.to_dict().get("status")
        if cur in ("completed", "failed", "cancelled"):
            return jsonify({"status": cur, "message": "任務已結束，無需取消"}), 200
        ref.update({"cancel_requested": True,
                    "updated_at": firestore.SERVER_TIMESTAMP})
        return jsonify({"status": "cancelling", "job_id": job_id}), 200
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e)}), 500


@app.route("/api/analyse/cleanup", methods=["POST"])
@require_api_key
def cleanup_jobs():
    """清除孤兒/陳舊分析任務文件（status 已結束且超過 days 天）。

    Request: {"days": 7}（預設 7）。回傳刪除筆數。
    analysis_jobs 是 pipeline 暫存層，結果回收進 content-analyser 後即可清理。
    """
    reaped = _reap()  # 先收割卡住的非終態任務（標 failed）
    import datetime
    data = request.get_json(silent=True) or {}
    try:
        days = max(0, int(data.get("days", 7)))
    except (TypeError, ValueError):
        days = 7
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    deleted = 0
    MAX_CLEAN = 500  # 單次上限，避免全表掃/刪導致逾時
    try:
        # 只查已結束狀態（伺服器端單欄位過濾，免複合索引）+ 上限，不再全表 stream。
        q = (db.collection(JOBS_COLLECTION)
             .where("status", "in", ["completed", "failed", "cancelled"])
             .limit(MAX_CLEAN))
        for doc in q.stream():
            d = doc.to_dict() or {}
            updated = d.get("updated_at") or d.get("completed_at")
            if updated is None or updated < cutoff:
                doc.reference.delete()
                deleted += 1
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e),
                        "deleted": deleted, "reaped": reaped}), 500
    return jsonify({"status": "ok", "deleted": deleted, "reaped": reaped, "days": days,
                    "capped": deleted >= MAX_CLEAN}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
