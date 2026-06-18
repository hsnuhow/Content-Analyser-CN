# -*- coding: utf-8 -*-
"""
批次爬取任務

兩種執行模式（同一套爬取序列 `_crawl_sequence`，差別只在如何被觸發/聚合）：
- **佇列模式（並行安全，正式）**：app.py 切塊 + Cloud Tasks 入列 → 每塊由 `run_crawl_chunk`
  在「請求生命週期內同步」跑（Cloud Run concurrency=1 → 每台一次只 1 個 Chrome），
  完成計數寫回 job 文件，全部塊完成才標 completed。
- **背景執行緒模式（fallback）**：佇列 env 未設定時，app.py 用背景 thread 呼叫 `run_crawl_batch`
  （舊行為，供 local / 未設佇列環境；多用戶並行有 OOM 風險，故正式環境應啟用佇列）。

進度與結果即時寫入 Firestore crawl_jobs/{job_id}，供呼叫端輪詢。
"""
import math
import time
import traceback
import concurrent.futures

from firebase_admin import firestore

from crawler import HeadlessCrawler, UnsupportedSiteError

JOBS_COLLECTION = "crawl_jobs"

# ── 防卡死 / 成本守衛常數 ──
RECYCLE_EVERY = 12           # 每爬 N 篇回收重建 driver，釋放 Chrome 記憶體（防 OOM）。
PAGE_HARD_TIMEOUT = 240      # 傳入 scrape 的內部（檢查點式）硬時限
PAGE_WATCHDOG = 290          # 單頁看門狗：超過代表卡在步驟內，強制中止
MAX_CONSECUTIVE_HANGS = 3    # 連續 N 篇看門狗逾時 → 疑系統性問題，提前中止
BATCH_MAX_SECONDS = 2700     # 整批（背景執行緒模式）總時限（45 分）backstop
CHUNK_SIZE = 6               # 佇列模式：每個 Cloud Tasks 任務處理幾個 URL（worst 6×240s<30min 派送上限）
CHUNK_MAX_SECONDS = 1500     # 單塊（單任務）時限（25 分）< Cloud Tasks 派送上限 30 分，留緩衝


def _update_job(db, job_id: str, **fields):
    try:
        db.collection(JOBS_COLLECTION).document(job_id).update({
            **fields,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"[CrawlJob] Firestore update 失敗: {e}", flush=True)


def _is_cancelled(db, job_id: str) -> bool:
    """合作式取消：cancel_requested=True 或文件被刪除 → 視為取消。"""
    try:
        snap = db.collection(JOBS_COLLECTION).document(job_id).get()
        if not snap.exists:
            return True
        return bool(snap.to_dict().get("cancel_requested"))
    except Exception:
        return False


def _write_result(db, job_id: str, idx: int, result: dict) -> None:
    """單篇結果寫入子集合 crawl_jobs/{job_id}/results/{idx}（idx 補零維持排序、retry 冪等覆蓋）。"""
    try:
        (db.collection(JOBS_COLLECTION).document(job_id)
         .collection("results").document(f"{idx:05d}").set(result))
    except Exception as e:
        print(f"[CrawlJob] 寫入 result {idx} 失敗: {e}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# 共用爬取序列（背景批次與佇列分塊都呼叫，確保看門狗/回收/Tier2-3 邏輯單一來源）
# ──────────────────────────────────────────────────────────────────────
def _crawl_sequence(urls, use_gemini, gemini_api_key, log, record_fn,
                    is_cancelled_fn=None, deadline_sec=BATCH_MAX_SECONDS,
                    force_listing=False) -> dict:
    """逐一爬取 urls（含看門狗 / driver 回收 / Tier2-3 / force_close），每篇呼叫
    record_fn(local_index, url, result) 由呼叫端持久化（決定全域 index 與進度）。
    回 {counts, aborted, processed}。aborted 可能為 'cancelled' / 時限說明 / None。"""
    counts = {"success": 0, "skipped": 0, "failed": 0}
    by_method = {}   # P1b：resolved_by 分布（learned/template/structured/heuristic/llm/...）

    def _new_crawler() -> HeadlessCrawler:
        c = HeadlessCrawler()
        if use_gemini and gemini_api_key:
            c.configure_genai(gemini_api_key)
        return c

    crawler = _new_crawler()

    def _proxied_scrape(url: str) -> dict:
        """Tier 3：用獨立的代理 crawler 重試（與重用的直連 driver 分開）。"""
        pc = HeadlessCrawler(use_proxy=True)
        if use_gemini and gemini_api_key:
            pc.configure_genai(gemini_api_key)
        try:
            res = pc.scrape(url, hard_timeout_sec=PAGE_HARD_TIMEOUT, force_listing=force_listing)
            if isinstance(res, dict):
                res["resolved_by"] = getattr(pc, "last_resolved_by", "") or res.get("source", "")
            return res
        except UnsupportedSiteError as e:
            return {"status": "skipped", "url": url, "error": str(e)}
        except Exception as e:
            return {"status": "failed", "url": url, "error": str(e)}
        finally:
            try:
                pc.close()
            except Exception:
                pass

    def _scrape_one(url: str) -> dict:
        try:
            result = crawler.scrape(url, hard_timeout_sec=PAGE_HARD_TIMEOUT, keep_driver=True,
                                    force_listing=force_listing)
            if isinstance(result, dict):
                result["resolved_by"] = getattr(crawler, "last_resolved_by", "") or result.get("source", "")
        except UnsupportedSiteError as e:
            return {"status": "skipped", "url": url, "error": str(e)}
        except Exception as e:
            return {"status": "failed", "url": url, "error": str(e)}
        try:
            from tiered_fallback import run_tier23
            return run_tier23(url, result, gemini_api_key,
                              proxied_scrape_fn=_proxied_scrape, log_fn=log)
        except Exception as e:
            log(f"[Tier2/3] 協調失敗（回退 Tier1）：{e}")
            return result

    def _scrape_with_watchdog(url: str):
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_scrape_one, url)
        try:
            return fut.result(timeout=PAGE_WATCHDOG), False
        except concurrent.futures.TimeoutError:
            return ({"status": "failed", "url": url,
                     "error": f"看門狗逾時（>{PAGE_WATCHDOG}s）已強制中止，頁面疑似無回應"}, True)
        finally:
            ex.shutdown(wait=False)

    def _force_close(c, timeout: int = 15):
        if c is None:
            return
        cex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            cex.submit(c.close).result(timeout=timeout)
        except Exception:
            try:
                drv = getattr(c, "driver", None)
                proc = getattr(getattr(drv, "service", None), "process", None)
                if proc:
                    proc.kill()
                    log("close() 逾時，已強制 kill Chrome 進程")
            except Exception:
                pass
        finally:
            cex.shutdown(wait=False)

    li = [0]

    def _emit(url, result):
        st = result.get("status")
        if st in counts:
            counts[st] += 1
        # P1b：成功篇才計 resolved_by 分布（失敗/略過另計）
        m = (result.get("resolved_by") or result.get("source") or st or "unknown") if st == "success" else st
        by_method[m] = by_method.get(m, 0) + 1
        record_fn(li[0], url, result)
        li[0] += 1

    aborted = None
    start_ts = time.time()
    consecutive_hangs = 0
    total = len(urls)
    try:
        for i, url in enumerate(urls):
            if is_cancelled_fn and is_cancelled_fn():
                aborted = "cancelled"
                break
            if time.time() - start_ts > deadline_sec:
                aborted = f"超過 {deadline_sec}s 時限"
                for u in urls[i:]:
                    _emit(u, {"status": "failed", "url": u,
                              "error": aborted + "，未爬取", "unattempted": True})
                break
            if i > 0 and i % RECYCLE_EVERY == 0:
                _force_close(crawler)
                crawler = _new_crawler()
                log(f"已回收並重建 driver（第 {i} 篇前），釋放記憶體")

            u = (url or "").strip()
            if not u:
                _emit(u, {"status": "failed", "url": u, "error": "Empty URL"})
                consecutive_hangs = 0
                continue
            if not (u.startswith("http://") or u.startswith("https://")):
                _emit(u, {"status": "failed", "url": u, "error": "Invalid URL"})
                consecutive_hangs = 0
                continue

            log(f"({i + 1}/{total}) {u}")
            _t0 = time.time()
            result, hung = _scrape_with_watchdog(u)
            result["elapsed_sec"] = round(time.time() - _t0, 1)
            result["hung"] = hung
            if hung:
                log("⏱ 看門狗逾時，強制砍掉並重建 driver（解除卡死的 Chrome）")
                _force_close(crawler)
                crawler = _new_crawler()
                consecutive_hangs += 1
            else:
                consecutive_hangs = 0
            _emit(u, result)

            if consecutive_hangs >= MAX_CONSECUTIVE_HANGS:
                aborted = f"連續 {consecutive_hangs} 篇看門狗逾時，疑系統性問題"
                for uu in urls[i + 1:]:
                    _emit(uu, {"status": "failed", "url": uu,
                               "error": aborted + "，未爬取", "unattempted": True})
                break
    finally:
        try:
            crawler.close()
        except Exception:
            pass
    return {"counts": counts, "aborted": aborted, "processed": li[0], "by_method": by_method}


def _write_telemetry(db, by_method: dict) -> None:
    """P1b：把本次爬取的 resolved_by 分布累加到 crawl_telemetry/global（Increment）。best-effort。
    用以觀察「多少篇靠 learned/template/structured/heuristic/llm 解出」→ 指導 P3/P4 投資。"""
    if not db or not by_method:
        return
    try:
        ref = db.collection("crawl_telemetry").document("global")
        ref.set({"by_method": {k: firestore.Increment(v) for k, v in by_method.items()},
                 "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
    except Exception as e:
        print(f"[CrawlJob] telemetry 寫入略過：{e}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# 背景執行緒模式（fallback，佇列未啟用時用）
# ──────────────────────────────────────────────────────────────────────
def run_crawl_batch(job_id: str, urls: list, use_gemini: bool,
                    gemini_api_key: str, db, force_listing: bool = False) -> None:
    """整批在單一背景執行緒內爬完（舊行為）。僅 fallback；多用戶並行有 OOM 風險。"""
    def _log(msg):
        print(f"[CrawlJob {job_id[:8]}] {msg}", flush=True)

    try:
        total = len(urls)
        _update_job(db, job_id, status="running", progress=2, total=total,
                    log=f"開始爬取 {total} 個網址...")

        def record(local_i, url, result):
            _write_result(db, job_id, local_i, result)
            prog = 2 + int((local_i + 1) / total * 95)
            st = result.get('status', '?')
            log = f"({local_i + 1}/{total}) {st}: {result.get('title') or url}"
            if st in ('failed', 'skipped') and result.get('error'):
                log += f" — {str(result['error'])[:120]}"
            _update_job(db, job_id, progress=prog, log=log)

        out = _crawl_sequence(urls, use_gemini, gemini_api_key, _log, record,
                              is_cancelled_fn=lambda: _is_cancelled(db, job_id),
                              deadline_sec=BATCH_MAX_SECONDS, force_listing=force_listing)
        _write_telemetry(db, out.get("by_method"))
        if out["aborted"] == "cancelled":
            _log("收到取消請求，停止爬取")
            _update_job(db, job_id, status="cancelled",
                        log=f"已取消（完成 {out['processed']}/{total}）")
            return
        c = out["counts"]
        done_log = (f"完成：成功 {c['success']}、略過 {c['skipped']}、失敗 {c['failed']}")
        if out["aborted"]:
            done_log = f"提前中止（{out['aborted']}）。{done_log}"
        _update_job(db, job_id, status="completed", progress=100, log=done_log,
                    succeeded=c["success"], skipped=c["skipped"], failed=c["failed"],
                    completed_at=firestore.SERVER_TIMESTAMP)
        _log(f"✅ 批次爬取結束：{done_log}")
    except Exception as e:
        _log(f"CRITICAL ERROR: {e}")
        traceback.print_exc()
        _update_job(db, job_id, status="failed", log=f"系統錯誤: {e}")


# ──────────────────────────────────────────────────────────────────────
# 佇列模式（並行安全，正式）：每塊一個同步 worker
# ──────────────────────────────────────────────────────────────────────
def _complete_chunk(db, job_id: str, chunk_index: int, n_chunks: int,
                    counts: dict, cancelled: bool = False) -> None:
    """交易式記錄『某塊已完成』+ 聚合計數；全部塊完成才標 completed。冪等（retry 覆蓋同 key）。"""
    ref = db.collection(JOBS_COLLECTION).document(job_id)
    transaction = db.transaction()

    @firestore.transactional
    def _txn(txn):
        snap = ref.get(transaction=txn)
        if not snap.exists:
            return
        d = snap.to_dict() or {}
        cd = d.get("chunks_done") or {}
        cd[str(chunk_index)] = {"s": counts["success"], "k": counts["skipped"],
                                "f": counts["failed"]}
        agg = {"s": 0, "k": 0, "f": 0}
        for v in cd.values():
            agg["s"] += v.get("s", 0)
            agg["k"] += v.get("k", 0)
            agg["f"] += v.get("f", 0)
        done_chunks = len(cd)
        cur = d.get("status")
        upd = {"chunks_done": cd, "succeeded": agg["s"], "skipped": agg["k"],
               "failed": agg["f"], "updated_at": firestore.SERVER_TIMESTAMP,
               "progress": min(99, int(done_chunks / max(1, n_chunks) * 100))}
        if cancelled and cur not in ("completed", "failed"):
            upd["status"] = "cancelled"
            upd["log"] = f"已取消（{done_chunks}/{n_chunks} 塊回報）"
        elif done_chunks >= n_chunks and cur not in ("cancelled", "failed"):
            upd["status"] = "completed"
            upd["progress"] = 100
            upd["completed_at"] = firestore.SERVER_TIMESTAMP
            upd["log"] = f"完成：成功 {agg['s']}、略過 {agg['k']}、失敗 {agg['f']}"
        elif cur not in ("completed", "cancelled", "failed"):
            upd["status"] = "running"
            upd["log"] = f"已完成 {done_chunks}/{n_chunks} 塊"
        txn.update(ref, upd)

    try:
        _txn(transaction)
    except Exception as e:
        print(f"[CrawlChunk] 完成計數交易失敗: {e}", flush=True)


def run_crawl_chunk(job_id: str, urls: list, chunk_index: int, n_chunks: int,
                    offset: int, use_gemini: bool, gemini_api_key: str, db,
                    force_listing: bool = False) -> None:
    """同步處理單一塊（Cloud Tasks worker 呼叫）。在請求生命週期內跑完 → 每台 instance 1 Chrome。"""
    def _log(msg):
        print(f"[CrawlChunk {job_id[:8]}#{chunk_index}] {msg}", flush=True)

    if _is_cancelled(db, job_id):
        _log("job 已取消，跳過此塊")
        _complete_chunk(db, job_id, chunk_index, n_chunks,
                        {"success": 0, "skipped": 0, "failed": 0}, cancelled=True)
        return

    # 逐篇即時回饋：佇列模式補回「每篇更新 job log/progress（含錯誤訊息）」，
    # 否則整塊跑完前畫面停在 0%、看不到錯誤（遷移時掉的回饋）。
    try:
        _snap = db.collection(JOBS_COLLECTION).document(job_id).get()
        _total = (_snap.to_dict() or {}).get('total', 0) if _snap.exists else 0
    except Exception:
        _total = 0

    def record(local_i, url, result):
        _write_result(db, job_id, offset + local_i, result)
        gi = offset + local_i + 1
        st = result.get('status', '?')
        title = (result.get('title') or url or '')[:60]
        log = f"({gi}/{_total or '?'}) {st}：{title}"
        if st in ('failed', 'skipped') and result.get('error'):
            log += f" — {str(result['error'])[:120]}"   # 佇列也顯示錯誤訊息
        fields = {'log': log}
        if _total:
            fields['progress'] = min(98, max(1, int(gi / _total * 100)))
        _update_job(db, job_id, **fields)

    try:
        out = _crawl_sequence(urls, use_gemini, gemini_api_key, _log, record,
                              is_cancelled_fn=lambda: _is_cancelled(db, job_id),
                              deadline_sec=CHUNK_MAX_SECONDS, force_listing=force_listing)
    except Exception as e:
        _update_job(db, job_id, log=f"塊 {chunk_index} 執行錯誤：{e}")  # 不再靜默；寫入後上拋讓 Cloud Tasks 重試
        raise
    _write_telemetry(db, out.get("by_method"))
    _complete_chunk(db, job_id, chunk_index, n_chunks, out["counts"],
                    cancelled=(out["aborted"] == "cancelled"))
    _log(f"塊完成：{out['counts']}（aborted={out['aborted']}）")


def chunk_urls(urls: list):
    """把 urls 切成 [(chunk_index, offset, [urls...]), ...]，供 app.py 入列。"""
    chunks = []
    for ci in range(math.ceil(len(urls) / CHUNK_SIZE)):
        offset = ci * CHUNK_SIZE
        chunks.append((ci, offset, urls[offset:offset + CHUNK_SIZE]))
    return chunks
