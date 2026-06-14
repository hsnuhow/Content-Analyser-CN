# -*- coding: utf-8 -*-
"""
非同步批次爬取任務

由 app.py 的背景 thread 呼叫。逐一爬取 URL，即時將進度與結果
寫入 Firestore crawl_jobs/{job_id}，供呼叫端（content-analyser UI / Colab）輪詢。
"""
import time
import traceback
import concurrent.futures

from firebase_admin import firestore

from crawler import HeadlessCrawler, UnsupportedSiteError

JOBS_COLLECTION = "crawl_jobs"

# ── 防卡死 / 成本守衛常數 ──
RECYCLE_EVERY = 6            # 每爬 N 篇回收重建 driver，釋放 Chrome 記憶體（防 OOM）
PAGE_HARD_TIMEOUT = 120      # 傳入 scrape 的內部（檢查點式）硬時限；正常頁多 <90s 完成
PAGE_WATCHDOG = 160          # 單頁看門狗：> 內部時限 + 緩衝；超過代表卡在步驟內，強制中止
MAX_CONSECUTIVE_HANGS = 3    # 連續 N 篇看門狗逾時 → 疑系統性問題，提前中止整批
BATCH_MAX_SECONDS = 2700     # 整批總時限（45 分）backstop，避免長批次無限耗時/費用（搭配重啟續爬）


def _update_job(db, job_id: str, **fields):
    try:
        db.collection(JOBS_COLLECTION).document(job_id).update({
            **fields,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"[CrawlJob] Firestore update 失敗: {e}", flush=True)


def _is_cancelled(db, job_id: str) -> bool:
    """合作式取消：讀 job 文件，若 cancel_requested=True 或文件已被刪除 → 視為取消。

    呼叫端（content-analyser）透過 POST /api/crawl/<id>/cancel 設旗標，
    或直接刪除文件。背景迴圈於每篇前檢查，收到即停止。
    """
    try:
        snap = db.collection(JOBS_COLLECTION).document(job_id).get()
        if not snap.exists:
            return True
        return bool(snap.to_dict().get("cancel_requested"))
    except Exception:
        return False


def _write_result(db, job_id: str, idx: int, result: dict) -> None:
    """單篇結果寫入子集合 crawl_jobs/{job_id}/results/{idx}，避免 job 文件超過 1MB
    （內嵌全部結果會在約 100 篇內文時撐爆單文件上限）。idx 補零以維持排序。"""
    try:
        (db.collection(JOBS_COLLECTION).document(job_id)
         .collection("results").document(f"{idx:05d}").set(result))
    except Exception as e:
        print(f"[CrawlJob] 寫入 result {idx} 失敗: {e}", flush=True)


def run_crawl_batch(job_id: str, urls: list, use_gemini: bool,
                    gemini_api_key: str, db) -> None:
    """背景執行：逐一爬取 urls，結果寫入 crawl_jobs/{job_id}。

    批次內重用同一個 HeadlessCrawler（driver），省去每篇的冷啟動
    （undetected-chromedriver 初始化約 40–50 秒）。driver 若 crash，
    scrape() 內會自動關閉，下一篇會重新初始化。
    """
    def _log(msg):
        print(f"[CrawlJob {job_id[:8]}] {msg}", flush=True)

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
            return pc.scrape(url, hard_timeout_sec=PAGE_HARD_TIMEOUT)
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
            # Tier 1：keep_driver=True，批次重用直連 driver，不在每篇結束時 quit。
            result = crawler.scrape(url, hard_timeout_sec=PAGE_HARD_TIMEOUT, keep_driver=True)
        except UnsupportedSiteError as e:
            return {"status": "skipped", "url": url, "error": str(e)}
        except Exception as e:
            return {"status": "failed", "url": url, "error": str(e)}
        # Tier 2/3：env 控制、預設關閉；未設定時直接回傳 Tier 1 結果。
        try:
            from tiered_fallback import run_tier23
            return run_tier23(url, result, gemini_api_key,
                              proxied_scrape_fn=_proxied_scrape, log_fn=_log)
        except Exception as e:
            _log(f"[Tier2/3] 協調失敗（回退 Tier1）：{e}")
            return result

    def _scrape_with_watchdog(url: str):
        """單頁硬性看門狗：在獨立 thread 跑 _scrape_one，超過 PAGE_WATCHDOG 仍未回 →
        判定卡死（步驟內 hang，內部 checkpoint 時限攔不住）。回 (result, hung: bool)。
        hung=True 時呼叫端須砍掉並重建 driver 以解除卡住的 Chrome。"""
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_scrape_one, url)
        try:
            return fut.result(timeout=PAGE_WATCHDOG), False
        except concurrent.futures.TimeoutError:
            return ({"status": "failed", "url": url,
                     "error": f"看門狗逾時（>{PAGE_WATCHDOG}s）已強制中止，頁面疑似無回應"}, True)
        finally:
            ex.shutdown(wait=False)  # 不等待：卡住的 thread 待 driver 被砍後自然結束

    try:
        total = len(urls)
        _update_job(db, job_id, status="running", progress=2, total=total,
                    log=f"開始爬取 {total} 個網址...")

        # 結果寫入子集合（不再內嵌於 job 文件）→ 無單文件 1MB 上限，批次數量不受限。
        counts = {"success": 0, "skipped": 0, "failed": 0}
        idx = 0

        def _record(result: dict) -> dict:
            nonlocal idx
            _write_result(db, job_id, idx, result)
            st = result.get("status")
            if st in counts:
                counts[st] += 1
            idx += 1
            return result

        start_ts = time.time()
        consecutive_hangs = 0
        aborted = None  # 提前中止原因（連續卡死 / 總時限）

        for i, url in enumerate(urls):
            # 合作式取消檢查：使用者強制停止 → 立即停止爬取。
            if _is_cancelled(db, job_id):
                _log("收到取消請求，停止爬取")
                _update_job(db, job_id, status="cancelled",
                            log=f"已取消（完成 {idx}/{total}）")
                return

            # 批次總時限 backstop：超過即收尾，剩餘標未爬取。
            if time.time() - start_ts > BATCH_MAX_SECONDS:
                aborted = f"批次超過 {BATCH_MAX_SECONDS}s 總時限"
                for u in urls[i:]:
                    _record({"status": "failed", "url": u, "error": aborted + "，未爬取"})
                break

            # 每 RECYCLE_EVERY 篇回收 driver，釋放 Chrome 記憶體（防長批次 OOM）。
            #   學到的選擇器已持久化於 Firestore，重建 driver 不會遺失（下次自動載回）。
            if i > 0 and i % RECYCLE_EVERY == 0:
                try:
                    crawler.close()
                except Exception:
                    pass
                crawler = _new_crawler()
                _log(f"已回收並重建 driver（第 {i} 篇前），釋放記憶體")

            url = (url or "").strip()
            if not url:
                result = _record({"status": "failed", "url": url, "error": "Empty URL"})
                consecutive_hangs = 0
            elif not (url.startswith("http://") or url.startswith("https://")):
                result = _record({"status": "failed", "url": url, "error": "Invalid URL"})
                consecutive_hangs = 0
            else:
                _log(f"({i+1}/{total}) {url}")
                result, hung = _scrape_with_watchdog(url)
                if hung:
                    _log(f"⏱ 看門狗逾時，強制砍掉並重建 driver（解除卡死的 Chrome）")
                    old = crawler
                    crawler = _new_crawler()      # 先換新，供下一篇
                    try:
                        old.close()               # 砍掉卡住的 driver → 解除 hang 中的 thread
                    except Exception:
                        pass
                    consecutive_hangs += 1
                else:
                    consecutive_hangs = 0
                _record(result)

            prog = 2 + int((i + 1) / total * 95)
            _update_job(
                db, job_id,
                progress=prog,
                log=f"({i+1}/{total}) {result.get('status')}: {result.get('title') or url}",
            )

            # 連續卡死中止：疑系統性問題（Chrome 壞 / 代理掛 / 站台全無回應），省費用。
            if consecutive_hangs >= MAX_CONSECUTIVE_HANGS:
                aborted = f"連續 {consecutive_hangs} 篇看門狗逾時，疑系統性問題"
                for u in urls[i + 1:]:
                    _record({"status": "failed", "url": u, "error": aborted + "，未爬取"})
                break

        done_log = (f"完成：成功 {counts['success']}、略過 {counts['skipped']}、"
                    f"失敗 {counts['failed']}")
        if aborted:
            done_log = f"提前中止（{aborted}）。{done_log}"

        _update_job(
            db, job_id,
            status="completed",
            progress=100,
            log=done_log,
            succeeded=counts["success"],
            skipped=counts["skipped"],
            failed=counts["failed"],
            completed_at=firestore.SERVER_TIMESTAMP,
        )
        _log(f"✅ 批次爬取結束：{done_log}")

    except Exception as e:
        _log(f"CRITICAL ERROR: {e}")
        traceback.print_exc()
        _update_job(db, job_id, status="failed", log=f"系統錯誤: {e}")
    finally:
        # 批次結束統一關閉重用的 driver。
        try:
            crawler.close()
        except Exception:
            pass
