# -*- coding: utf-8 -*-
"""
分析 Pipeline 主協調器

流程：
  1. 兩路平行執行（threading）：
     - Path 1：nlp_path.run()  → 數值分析（TF-IDF + Vertex AI 分群）
     - Path 2：llm_path.run()  → LLM 直讀（搜尋意圖 + 質化分析）
  2. 兩路完成後：synthesis.run() → 詮釋性章節
  3. report.assemble() → 最終 Markdown 報告
  4. 全程將進度寫入 Firestore analysis_jobs/{job_id}
"""
import os
import threading
import traceback
from typing import List, Dict, Any

from firebase_admin import firestore

from llm_client import LLMClient
from nlp_path import run as run_nlp
from llm_path import run as run_llm
import synthesis
import report

# Firestore collection 名稱（analysis-pipeline 自管）
JOBS_COLLECTION = "analysis_jobs"


def _update_job(db, job_id: str, **fields):
    """安全地更新 Firestore job 文件。"""
    try:
        db.collection(JOBS_COLLECTION).document(job_id).update({
            **fields,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"[Pipeline] Firestore update 失敗：{e}", flush=True)


def run_analysis(job_id: str, report_title: str,
                 contents: List[Dict], llm_config: Dict, db) -> None:
    """
    背景執行的分析任務主函式。由 app.py 的背景 thread 呼叫。

    Args:
        job_id:       Firestore 中的 analysis_jobs/{job_id}
        report_title: 報告標題
        contents:     [{url, title, text, source_type}, ...]
        llm_config:   {provider, model, api_key}
        db:           Firestore client
    """
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

    def _log(msg: str):
        print(f"[Job {job_id[:8]}] {msg}", flush=True)

    def _progress(prog: int, log: str):
        _log(log)
        _update_job(db, job_id, progress=prog, log=log)

    try:
        _update_job(db, job_id, status="running", progress=5, log="分析任務啟動...")

        # ── 建立 LLM client ──
        try:
            llm = LLMClient(
                provider=llm_config.get("provider", "gemini"),
                model=llm_config.get("model", "gemini-2.5-flash"),
                api_key=llm_config.get("api_key", ""),
            )
        except ValueError as e:
            _update_job(db, job_id, status="failed", log=f"LLM 設定錯誤：{e}")
            return

        # ── 兩路平行執行 ──
        nlp_results: Dict[str, Any] = {}
        llm_results: Dict[str, Any] = {}
        nlp_error: list = []
        llm_error: list = []

        def _run_path1():
            try:
                _progress(10, "Path 1：TF-IDF 關鍵字分析...")
                result = run_nlp(
                    contents=contents,
                    project_id=project_id,
                    log_fn=lambda m: _update_job(db, job_id, log=m),
                )
                nlp_results.update(result)
                _progress(40, f"Path 1 完成：{result['clusters'].get('n_clusters', 0)} 個語意群組")
            except Exception as e:
                nlp_error.append(str(e))
                _log(f"Path 1 失敗：{e}")
                traceback.print_exc()

        def _run_path2():
            try:
                _progress(15, "Path 2：LLM 搜尋意圖萃取與質化分析...")
                result = run_llm(
                    contents=contents,
                    llm=llm,
                    log_fn=lambda m: _update_job(db, job_id, log=m),
                )
                llm_results.update(result)
                n_intents = sum(len(a["search_intents"]) for a in result["search_intents"])
                _progress(70, f"Path 2 完成：{n_intents} 個搜尋情境萃取完畢")
            except Exception as e:
                llm_error.append(str(e))
                _log(f"Path 2 失敗：{e}")
                traceback.print_exc()

        t1 = threading.Thread(target=_run_path1, daemon=True)
        t2 = threading.Thread(target=_run_path2, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # ── 路徑失敗處理 ──
        if llm_error:
            # Path 2 失敗（LLM Key 問題）→ 任務失敗
            err_msg = f"LLM 分析失敗，請確認 API Key 與模型設定：{llm_error[0]}"
            _update_job(db, job_id, status="failed", log=err_msg)
            return

        if nlp_error:
            # Path 1 失敗（Vertex AI 問題）→ 降級繼續（只有 TF-IDF）
            _log(f"⚠️ Path 1 部分失敗，降級繼續（無語意分群）：{nlp_error[0]}")
            if not nlp_results:
                nlp_results = {"tfidf": {"top_keywords": [], "per_article": []},
                               "clusters": {"clusters": [], "n_clusters": 0}}

        # ── Synthesis ──
        _progress(80, "Synthesis：整合數值與質化結果，生成報告...")
        synthesis_parts = synthesis.run(
            nlp_results=nlp_results,
            llm_results=llm_results,
            report_title=report_title,
            n_articles=len(contents),
            llm=llm,
        )

        # ── 組裝最終報告 ──
        _progress(93, "組裝最終 Markdown 報告...")
        final_md = report.assemble(
            report_title=report_title,
            contents=contents,
            nlp_results=nlp_results,
            llm_results=llm_results,
            synthesis_parts=synthesis_parts,
            llm_provider=llm_config.get("provider", "gemini"),
            llm_model=llm_config.get("model", "gemini-2.5-flash"),
        )

        _update_job(
            db, job_id,
            status="completed",
            progress=100,
            log="分析完成！",
            result_markdown=final_md,
            completed_at=firestore.SERVER_TIMESTAMP,
        )
        _log("✅ 分析任務完成")

    except Exception as e:
        _log(f"CRITICAL ERROR：{e}")
        traceback.print_exc()
        _update_job(
            db, job_id,
            status="failed",
            log=f"系統錯誤：{e}",
        )
