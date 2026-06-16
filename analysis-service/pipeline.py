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
import search_extent_client

# Firestore collection 名稱（analysis-pipeline 自管）
JOBS_COLLECTION = "analysis_jobs"

# search-extent：每次分析最多展開幾個語意群（控制 Ads API 配額）、每群取幾個種子詞。
MAX_SEARCH_EXTENT_CLUSTERS = 6
SEARCH_EXTENT_SEEDS_PER_CLUSTER = 5


def _update_job(db, job_id: str, **fields):
    """安全地更新 Firestore job 文件。"""
    try:
        db.collection(JOBS_COLLECTION).document(job_id).update({
            **fields,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"[Pipeline] Firestore update 失敗：{e}", flush=True)


def _is_cancelled(db, job_id: str) -> bool:
    """合作式取消：讀 job 文件，cancel_requested=True 或文件已刪除 → 視為取消。

    呼叫端透過 POST /api/analyse/<id>/cancel 設旗標。pipeline 於各檢查點檢查，
    收到即停止後續（含昂貴的 Synthesis LLM 呼叫）。
    """
    try:
        snap = db.collection(JOBS_COLLECTION).document(job_id).get()
        if not snap.exists:
            return True
        return bool(snap.to_dict().get("cancel_requested"))
    except Exception:
        return False


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

    def _cancelled_stop() -> bool:
        """檢查點：若已取消，標記 cancelled 並回傳 True（呼叫端應 return）。"""
        if _is_cancelled(db, job_id):
            _log("收到取消請求，停止分析")
            _update_job(db, job_id, status="cancelled", log="已取消")
            return True
        return False

    try:
        _update_job(db, job_id, status="running", progress=5, log="分析任務啟動...")
        if _cancelled_stop():
            return

        # ── 建立 LLM client ──
        try:
            llm = LLMClient(
                provider=llm_config.get("provider", "gemini"),
                model=llm_config.get("model", "gemini-2.5-flash"),
                api_key=llm_config.get("api_key", ""),
                temperature=llm_config.get("temperature", 0.3),
                thinking=llm_config.get("thinking", False),
                max_tokens=llm_config.get("max_output_tokens") or 8192,
                top_p=llm_config.get("top_p"),
            )
        except ValueError as e:
            _update_job(db, job_id, status="failed", log=f"LLM 設定錯誤：{e}")
            return

        # ── search-extent 開關：URL/Key 已設定 + 本次未停用（預設開）──
        se_enabled = (search_extent_client.is_enabled()
                      and bool(llm_config.get("search_extent", True)))
        search_extent_results: Dict[int, Any] = {}

        # ── 兩路平行執行 ──
        nlp_results: Dict[str, Any] = {}
        llm_results: Dict[str, Any] = {}
        nlp_error: list = []
        llm_error: list = []

        def _run_search_extent(clusters: Dict):
            """用各語意群 top 關鍵字呼叫 search-extent，取真實關聯關鍵字。
            在 Path 1 thread 內、分群後執行（與 Path 2 並行）。失敗只記 log、不影響報告。"""
            groups = clusters.get("clusters", [])
            if not groups:
                return
            done = 0
            for g in groups[:MAX_SEARCH_EXTENT_CLUSTERS]:
                seeds = [k for k in (g.get("keywords") or [])[:SEARCH_EXTENT_SEEDS_PER_CLUSTER] if k]
                if not seeds:
                    continue
                res = search_extent_client.expand(seeds, limit=60)
                if not isinstance(res, dict) or "error" in res:
                    _log(f"[search-extent] 群 {g.get('cluster_id', 0) + 1} 略過：{(res or {}).get('error')}")
                    continue
                ideas = res.get("ideas", []) or []
                if ideas:
                    search_extent_results[g["cluster_id"]] = {
                        "label": g.get("label", ""),
                        "seeds": seeds,
                        "ideas": ideas[:30],
                    }
                    done += 1
            if done:
                _log(f"[search-extent] 完成 {done} 群真實關聯關鍵字接地")

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
                # 為主題群生成 LLM 描述（label + 一句話定位）。
                # 移到 Path 1 thread 內（與 Path 2 並行，省一段序列等待）；
                # 且先於 search-extent，使其能用到正式群標籤。失敗不影響報告。
                try:
                    _progress(42, "為語意主題群生成描述...")
                    synthesis.label_clusters(result.get("clusters", {}), llm)
                except Exception as e:
                    _log(f"分群描述生成略過：{e}")
                # 分群完成 → 接 search-extent 真實搜尋接地（與 Path 2 並行）
                if se_enabled:
                    try:
                        _progress(45, "search-extent：取真實關聯關鍵字（Google Ads Keyword Planner）...")
                        _run_search_extent(result.get("clusters", {}))
                    except Exception as e:
                        _log(f"[search-extent] 整體略過（不影響報告）：{e}")
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
                    input_scale=llm_config.get("input_scale", "standard"),
                )
                llm_results.update(result)
                n_intents = sum(len(a.get("search_intents", [])) for a in result.get("search_intents", []))
                _progress(70, f"Path 2 完成：{n_intents} 個搜尋情境萃取完畢")
            except Exception as e:
                llm_error.append(str(e))
                _log(f"Path 2 失敗：{e}")
                traceback.print_exc()

        # ── 閘門：先跑 Path 1（數值語意探勘），數值層失敗就不進 LLM ──
        # 使用者要求：「數值文本分析失敗時，不會進入 LLM 分析」。數值層（TF-IDF 為核心，
        # 外加 Vertex 分群／關聯規則／Cloud NL）是整份報告的接地依據；沒有數值結果就不該
        # 燒 LLM token 產出無依據的報告。因此 Path 1 先行、作為 Path 2 的前置閘門。
        # （Vertex embedding 批量已調大、加重試，Path 1 通常 1–2 分鐘完成，序列化成本可接受。）
        t1 = threading.Thread(target=_run_path1, daemon=True)
        t1.start()
        t1.join(timeout=600)
        path1_timed_out = t1.is_alive()
        if path1_timed_out:
            nlp_error.append("Path 1 超過 600s 未完成，已放棄等待")
            _log("⚠️ Path 1 thread 逾時（600s）")

        # Path 1 逾時：run_nlp 早已把 tfidf/clusters 寫入 nlp_results，逾時多半卡在其後的
        # label_clusters / search-extent（仍在 daemon thread 內）。保留已完成的數值結果
        # （TF-IDF 是閘門核心），只清空仍可能被 thread 寫入的 search_extent，避免 race。
        if path1_timed_out:
            search_extent_results = {}
            _log("⚠️ Path 1 逾時，保留已完成數值結果、清空 search-extent")

        # ── 數值層閘門判定 ──
        # 數值核心 = TF-IDF top_keywords。沒有它就代表數值探勘整體失敗 → 不進 LLM、直接失敗。
        tfidf_ok = bool((nlp_results.get("tfidf") or {}).get("top_keywords"))
        if not tfidf_ok:
            reason = (nlp_error[0] if nlp_error else "TF-IDF 未產生關鍵字")
            err_msg = f"數值語意探勘失敗，已中止（不進入 LLM 分析）：{reason}"
            _update_job(db, job_id, status="failed", log=err_msg)
            _log(f"⛔ {err_msg}")
            return
        # TF-IDF 成功但分群/關聯/實體可降級——僅記 log，繼續進 LLM。
        if nlp_error:
            _log(f"⚠️ 數值層部分降級（核心 TF-IDF 仍有效）：{nlp_error[0]}")

        if _cancelled_stop():
            return

        # ── 數值層通過閘門 → 執行 Path 2（LLM 質化分析）──
        t2 = threading.Thread(target=_run_path2, daemon=True)
        t2.start()
        t2.join(timeout=600)
        if t2.is_alive():
            llm_error.append("Path 2 超過 600s 未完成，已放棄等待")
            _log("⚠️ Path 2 thread 逾時（600s），停止任務")

        if llm_error:
            # Path 2 失敗（LLM Key 問題）→ 任務失敗
            err_msg = f"LLM 分析失敗，請確認 API Key 與模型設定：{llm_error[0]}"
            _update_job(db, job_id, status="failed", log=err_msg)
            return

        # 凍結 search-extent 結果快照：synthesis（決定 §7 prompt 版本）與 report
        # （決定是否標「真實接地」）共用同一份，避免各自重判 dict 是否為空而不一致。
        se_frozen = dict(search_extent_results)

        # 兩路完成 → 進入昂貴的 Synthesis 前先檢查取消。
        if _cancelled_stop():
            return

        # ── Synthesis ──
        _progress(80, "Synthesis：整合數值與質化結果，生成報告...")
        synthesis_parts = synthesis.run(
            nlp_results=nlp_results,
            llm_results=llm_results,
            report_title=report_title,
            n_articles=len(contents),
            llm=llm,
            search_extent_results=se_frozen,
        )

        if _cancelled_stop():
            return

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
            search_extent_results=se_frozen,
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
