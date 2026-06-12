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


def _scrape_one(url: str, use_gemini: bool, gemini_api_key: str) -> dict:
    crawler = HeadlessCrawler()
    try:
        if use_gemini and gemini_api_key:
            crawler.configure_genai(gemini_api_key)
        return crawler.scrape(url, hard_timeout_sec=90)
    except UnsupportedSiteError as e:
        return {"status": "skipped", "url": url, "error": str(e)}
    except Exception as e:
        return {"status": "failed", "url": url, "error": str(e)}
    finally:
        crawler.close()


def run_crawl_batch(job_id: str, urls: list, use_gemini: bool,
                    gemini_api_key: str, db) -> None:
    """背景執行：逐一爬取 urls，結果寫入 crawl_jobs/{job_id}。"""
    def _log(msg):
        print(f"[CrawlJob {job_id[:8]}] {msg}", flush=True)

    try:
        total = len(urls)
        _update_job(db, job_id, status="running", progress=2,
                    log=f"開始爬取 {total} 個網址...")

        results = []
        for i, url in enumerate(urls):
            url = (url or "").strip()
            if not url:
                results.append({"status": "failed", "url": url, "error": "Empty URL"})
            elif not (url.startswith("http://") or url.startswith("https://")):
                results.append({"status": "failed", "url": url, "error": "Invalid URL"})
            else:
                _log(f"({i+1}/{total}) {url}")
                results.append(_scrape_one(url, use_gemini, gemini_api_key))

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
