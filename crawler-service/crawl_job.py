# -*- coding: utf-8 -*-
"""
非同步批次爬取任務

由 app.py 的背景 thread 呼叫。逐一爬取 URL，即時將進度與結果
寫入 Firestore crawl_jobs/{job_id}，供呼叫端（content-analyser UI / Colab）輪詢。
"""
import traceback

from firebase_admin import firestore

from crawler import HeadlessCrawler, UnsupportedSiteError

JOBS_COLLECTION = "crawl_jobs"


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


def run_crawl_batch(job_id: str, urls: list, use_gemini: bool,
                    gemini_api_key: str, db) -> None:
    """背景執行：逐一爬取 urls，結果寫入 crawl_jobs/{job_id}。

    批次內重用同一個 HeadlessCrawler（driver），省去每篇的冷啟動
    （undetected-chromedriver 初始化約 40–50 秒）。driver 若 crash，
    scrape() 內會自動關閉，下一篇會重新初始化。
    """
    def _log(msg):
        print(f"[CrawlJob {job_id[:8]}] {msg}", flush=True)

    crawler = HeadlessCrawler()
    if use_gemini and gemini_api_key:
        crawler.configure_genai(gemini_api_key)

    def _proxied_scrape(url: str) -> dict:
        """Tier 3：用獨立的代理 crawler 重試（與重用的直連 driver 分開）。"""
        pc = HeadlessCrawler(use_proxy=True)
        if use_gemini and gemini_api_key:
            pc.configure_genai(gemini_api_key)
        try:
            return pc.scrape(url, hard_timeout_sec=300)
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
            result = crawler.scrape(url, hard_timeout_sec=300, keep_driver=True)
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

    try:
        total = len(urls)
        _update_job(db, job_id, status="running", progress=2,
                    log=f"開始爬取 {total} 個網址...")

        results = []
        for i, url in enumerate(urls):
            # 合作式取消檢查：使用者強制停止 → 立即停止爬取。
            if _is_cancelled(db, job_id):
                _log("收到取消請求，停止爬取")
                _update_job(db, job_id, status="cancelled",
                            log=f"已取消（完成 {i}/{total}）")
                return
            url = (url or "").strip()
            if not url:
                results.append({"status": "failed", "url": url, "error": "Empty URL"})
            elif not (url.startswith("http://") or url.startswith("https://")):
                results.append({"status": "failed", "url": url, "error": "Invalid URL"})
            else:
                _log(f"({i+1}/{total}) {url}")
                results.append(_scrape_one(url))

            prog = 2 + int((i + 1) / total * 95)
            last = results[-1]
            _update_job(
                db, job_id,
                progress=prog,
                log=f"({i+1}/{total}) {last.get('status')}: {last.get('title') or url}",
                results=results,
            )

        succeeded = sum(1 for r in results if r.get("status") == "success")
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        failed = sum(1 for r in results if r.get("status") == "failed")

        _update_job(
            db, job_id,
            status="completed",
            progress=100,
            log=f"完成：成功 {succeeded}、略過 {skipped}、失敗 {failed}",
            results=results,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            completed_at=firestore.SERVER_TIMESTAMP,
        )
        _log("✅ 批次爬取完成")

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
