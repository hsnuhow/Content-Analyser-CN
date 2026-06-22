# -*- coding: utf-8 -*-
"""
獨立爬蟲服務 API 入口 (Cloud Run)

完全獨立、透過 API 操作的無頭瀏覽器爬蟲服務。
所有 /api/* 端點需 X-API-Key（系統金鑰或 api_keys 白名單，需 'crawl' 權限）。

端點：
  GET  /health              健康檢查（不需金鑰）。
  POST /api/scrape          爬取單一網址（同步）。
  POST /api/scrape/batch    爬取多網址（同步，最多 20）。
  POST /api/crawl/batch     爬取多網址（非同步），回傳 job_id。
  GET  /api/crawl/{job_id}  查詢非同步爬取進度與結果。
  POST /api/extract-images  擷取多網址主文大圖（非同步），回傳 job_id。
  GET  /api/extract-images/{job_id}  查詢影像擷取進度與結果。

回傳格式（單篇）：
  成功: {"status": "success", "url", "title", "content", "length"}
  略過: {"status": "skipped", "url", "error"}
  失敗: {"status": "failed",  "url", "error"}
"""
import os
import uuid
import threading
import functools
import subprocess
import concurrent.futures

import firebase_admin
from firebase_admin import firestore
from flask import Flask, request, jsonify

from crawler import HeadlessCrawler, UnsupportedSiteError
from auth import is_authorized
from crawl_job import run_crawl_batch, JOBS_COLLECTION
from net_guard import is_safe_url as _is_safe_url

RESEARCH_JOBS = "research_jobs"
IMAGE_JOBS = "image_extract_jobs"
_REAP_COLLECTIONS = [JOBS_COLLECTION, IMAGE_JOBS, RESEARCH_JOBS]  # crawler 自管的 3 個 job 集合

SERVICE_VERSION = "1.8.0"


def _reap():
    """收割本服務 3 個集合的卡住任務（reap-on-submit / cleanup 觸發，全自動、零外部排程）。"""
    try:
        from reaper import reap_stale
        return reap_stale(db, _REAP_COLLECTIONS)
    except Exception as e:
        print(f"[Reaper] 觸發失敗（略過）: {e}", flush=True)
        return 0


# ── SSRF 守門已抽至 net_guard.py（is_safe_url / safe_urlopen）──
# app.py 透過 `from net_guard import is_safe_url as _is_safe_url` 取用，呼叫點不變。

# ── Firebase 初始化（供非同步 job 與 api_keys 驗證）──
db = None
if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app()
        print("[Firebase] Initialized with ADC.", flush=True)
    except Exception as e:
        print(f"[Firebase] Init failed: {e}", flush=True)
try:
    db = firestore.client()
except Exception as e:
    print(f"[Firebase] firestore client 取得失敗: {e}", flush=True)

app = Flask(__name__)

CRAWLER_API_KEY = os.environ.get("CRAWLER_API_KEY")
if not CRAWLER_API_KEY:
    print("[WARNING] CRAWLER_API_KEY 未設定，僅 api_keys 白名單可通過驗證。", flush=True)


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if not is_authorized(provided, CRAWLER_API_KEY, "crawl", db):
            return jsonify({
                "status": "failed",
                "error": "Unauthorized: missing or invalid X-API-Key（需 'crawl' 權限）"
            }), 401
        return f(*args, **kwargs)
    return wrapper


def _get_chrome_version():
    try:
        out = subprocess.check_output(
            ["google-chrome", "--version"], stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
        return out
    except Exception:
        return "unavailable"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "content-crawler",
        "version": SERVICE_VERSION,
        "chrome": _get_chrome_version(),
        "api_key_configured": bool(CRAWLER_API_KEY),
        "firebase": "connected" if db is not None else "unavailable",
    }), 200


def _force_close(c, timeout: int = 15):
    """關閉 driver，對 close() 本身加超時上限——避免 close() 卡住整個請求生命週期。
    逾時則直接 kill Chrome 進程（對齊 crawl_job 非同步看門狗）。"""
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
                print("[scrape] close() 逾時，已強制 kill Chrome 進程", flush=True)
        except Exception:
            pass
    finally:
        cex.shutdown(wait=False)


def _tier1_scrape(url: str, use_gemini: bool, gemini_api_key: str,
                  hard_timeout_sec: int, use_proxy: bool = False) -> dict:
    """Tier 1：undetected-chromedriver 爬取（use_proxy=True 時走 Tier 3 代理）。

    看門狗：scrape 步驟內若 Selenium 指令 hang，scrape 內部 hard_timeout 擋不住（卡在阻塞
    呼叫），故在外層包 ThreadPoolExecutor + result(timeout) 強制上限；close() 亦走 _force_close
    加超時。補上同步 /api/scrape 路徑原本缺、但非同步 crawl_job 早有的防護（單篇 hang / close 卡死）。
    """
    crawler = HeadlessCrawler(use_proxy=use_proxy)
    try:
        if use_gemini and gemini_api_key:
            crawler.configure_genai(gemini_api_key)
        # 看門狗上限留在 Cloud Run 300s 請求上限內（對齊 crawl_job PAGE_WATCHDOG=290）。
        watchdog = min(hard_timeout_sec + 30, 290)
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(crawler.scrape, url, hard_timeout_sec=hard_timeout_sec)
            return fut.result(timeout=watchdog)
        except concurrent.futures.TimeoutError:
            return {"status": "failed", "url": url,
                    "error": f"看門狗逾時（>{watchdog}s）已中止，頁面疑似無回應"}
        finally:
            ex.shutdown(wait=False)
    except UnsupportedSiteError as e:
        return {"status": "skipped", "url": url, "error": str(e)}
    except Exception as e:
        return {"status": "failed", "url": url, "error": str(e)}
    finally:
        _force_close(crawler)


def _run_scrape(url: str, use_gemini: bool, gemini_api_key: str,
                hard_timeout_sec: int = 60) -> dict:
    """分層爬取協調器（Tier 1 → 3；Tier 2 Gemini 直讀已廢除）。

    Tier 3（住宅代理）由環境變數控制、預設關閉：未設定時行為與單純 Tier 1 完全相同。
    見 tiered_fallback.py 與 CRAWLER_STRATEGY.md。
    """
    # ── Tier 1：無頭瀏覽器（直連）──
    result = _tier1_scrape(url, use_gemini, gemini_api_key, hard_timeout_sec)

    # ── Tier 3：未達標時用住宅代理重抓一次（獨立 crawler）。Tier 2(Gemini)已廢除。──
    try:
        from tiered_fallback import run_tier3
        return run_tier3(
            url, result,
            proxied_scrape_fn=lambda u: _tier1_scrape(
                u, use_gemini, gemini_api_key, hard_timeout_sec, use_proxy=True),
            log_fn=lambda m: print(m, flush=True),
        )
    except Exception as e:
        print(f"[Tier3] 協調失敗（回退 Tier1 結果）：{e}", flush=True)
        return result


# ── 同步端點（向後相容）──

@app.route("/api/scrape", methods=["POST"])
@require_api_key
def scrape():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"status": "failed", "error": "Missing 'url' in request body"}), 400
    safe, reason = _is_safe_url(url)
    if not safe:
        return jsonify({"status": "failed", "url": url, "error": f"URL 不合法：{reason}"}), 400

    use_gemini = bool(data.get("use_gemini", False))
    gemini_api_key = data.get("gemini_api_key") or os.environ.get("GENAI_API_KEY")
    # 防呆 + 夾值：非數字不致 500；上限 300s 避免單一請求被惡意/誤設綁住 worker。
    try:
        hard_timeout_sec = int(data.get("hard_timeout_sec", 60))
    except (TypeError, ValueError):
        hard_timeout_sec = 60
    hard_timeout_sec = min(max(hard_timeout_sec, 5), 300)

    result = _run_scrape(url, use_gemini, gemini_api_key, hard_timeout_sec)
    http_status = 200 if result.get("status") in ("success", "skipped") else 500
    return jsonify(result), http_status


@app.route("/api/scrape/batch", methods=["POST"])
@require_api_key
def scrape_batch():
    """同步批次爬取（最多 20，依序執行）。適合少量、可等待的場景。"""
    data = request.get_json(silent=True) or {}
    urls = data.get("urls") or []

    if not isinstance(urls, list) or not urls:
        return jsonify({"status": "failed", "error": "Missing or empty 'urls' list"}), 400
    if len(urls) > 20:
        return jsonify({"status": "failed", "error": "Maximum 20 URLs per batch request"}), 400

    use_gemini = bool(data.get("use_gemini", False))
    gemini_api_key = data.get("gemini_api_key") or os.environ.get("GENAI_API_KEY")

    results = []
    for url in urls:
        url = (url or "").strip()
        if not url:
            results.append({"status": "failed", "url": url, "error": "Empty URL"})
            continue
        safe, reason = _is_safe_url(url)
        if not safe:
            results.append({"status": "failed", "url": url, "error": f"URL 不合法：{reason}"})
            continue
        results.append(_run_scrape(url, use_gemini, gemini_api_key))

    succeeded = sum(1 for r in results if r.get("status") == "success")
    failed = sum(1 for r in results if r.get("status") == "failed")
    return jsonify({"results": results, "total": len(results),
                    "succeeded": succeeded, "failed": failed}), 200


# ── 非同步端點（建議用於 UI / Colab 大批量）──

@app.route("/api/crawl/batch", methods=["POST"])
@require_api_key
def crawl_batch():
    """非同步批次爬取，回傳 job_id。背景逐一爬取，結果存 Firestore crawl_jobs。

    Request: {"urls": [...], "use_gemini": false, "gemini_api_key": "..."}
    Response: {"job_id": "...", "status": "pending"}
    """
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線，無法建立非同步任務"}), 503
    _reap()  # reap-on-submit：每次提交先收割卡住任務（全自動）

    data = request.get_json(silent=True) or {}
    urls = data.get("urls") or []
    if not isinstance(urls, list) or not urls:
        return jsonify({"status": "failed", "error": "Missing or empty 'urls' list"}), 400
    if len(urls) > 1000:
        return jsonify({"status": "failed", "error": "Maximum 1000 URLs per crawl job"}), 400
    # C1: 先過濾掉不安全 URL，回報給呼叫端
    safe_urls, blocked = [], []
    for u in urls:
        ok, reason = _is_safe_url((u or "").strip())
        if ok:
            safe_urls.append(u)
        else:
            blocked.append({"url": u, "reason": reason})
    # 逐 URL 跳過（非全有全無）：只有「全部都被擋」才整批失敗；否則照爬安全的、把被擋的記在 job 上回報。
    if not safe_urls:
        return jsonify({
            "status": "failed",
            "error": f"全部 {len(blocked)} 個 URL 被 SSRF 過濾拒絕（無可爬取）",
            "blocked": blocked,
        }), 400

    use_gemini = bool(data.get("use_gemini", False))
    # 金鑰不入列（Cloud Tasks body 明文持久化於佇列）：使用者自帶金鑰存進 access-controlled
    # 的 job doc，worker 讀回；系統金鑰（GENAI_API_KEY）只在 worker 端從 env/Secret 解析，
    # 永不進佇列 body。fallback 背景執行緒走 in-process 記憶體（不持久化），用解析後的值即可。
    user_gemini_key = data.get("gemini_api_key") or ""
    gemini_api_key = user_gemini_key or os.environ.get("GENAI_API_KEY")
    force_listing = bool(data.get("force_listing", False))   # 強制爬取列表/商品頁（不略過）
    urls = safe_urls

    job_id = str(uuid.uuid4())
    from crawl_job import chunk_urls
    import task_queue
    chunks = chunk_urls(urls)
    use_queue = task_queue.tasks_enabled()
    _blk_note = f"（{len(blocked)} 個 URL 被安全過濾跳過）" if blocked else ""
    db.collection(JOBS_COLLECTION).document(job_id).set({
        "job_id": job_id,
        "status": "queued" if use_queue else "pending",
        "progress": 0,
        "log": f"任務已建立，等待執行...{_blk_note}",
        "total": len(urls),
        "n_chunks": len(chunks),
        "chunks_done": {},
        "blocked": blocked,          # 被 SSRF 過濾擋下的 URL（含 reason），供前端顯示
        "n_blocked": len(blocked),
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "completed_at": None,
        # 使用者自帶金鑰存於此（access-controlled）；worker 讀回，不放入 Cloud Tasks body。
        "gemini_api_key": user_gemini_key,
    })

    if use_queue:
        # 並行安全：切塊入列 Cloud Tasks，每塊由 /api/crawl/run 同步處理（concurrency=1）。
        # 注意：body 不含 gemini_api_key（金鑰由 worker 從 job doc / env 取，避免明文入佇列）。
        enq_ok = 0
        for ci, offset, chunk in chunks:
            if task_queue.enqueue("/api/crawl/run", {
                "job_id": job_id, "urls": chunk, "chunk_index": ci,
                "n_chunks": len(chunks), "offset": offset,
                "use_gemini": use_gemini,
                "force_listing": force_listing,
            }):
                enq_ok += 1
        if enq_ok < len(chunks):
            # 入列未全部成功 → 回報，避免任務「永遠跑不完」
            db.collection(JOBS_COLLECTION).document(job_id).update({
                "status": "failed",
                "log": f"入列失敗（{enq_ok}/{len(chunks)} 塊成功）",
                "updated_at": firestore.SERVER_TIMESTAMP})
            return jsonify({"status": "failed",
                            "error": f"Cloud Tasks 入列失敗（{enq_ok}/{len(chunks)}）"}), 502
    else:
        # Fallback（佇列未設定）：單一背景執行緒爬完。多用戶並行有 OOM 風險。
        threading.Thread(target=run_crawl_batch,
                         args=(job_id, urls, use_gemini, gemini_api_key, db, force_listing),
                         daemon=True).start()

    return jsonify({"job_id": job_id, "status": "queued" if use_queue else "pending",
                    "n_blocked": len(blocked), "blocked": blocked}), 202


@app.route("/api/crawl/run", methods=["POST"])
@require_api_key
def crawl_run():
    """Cloud Tasks worker：同步處理單一塊（請求生命週期內跑完，concurrency=1 → 1 Chrome/instance）。
    成功回 200；例外回 500 讓 Cloud Tasks 重試（結果 doc id 以全域 index 命名 → 重試冪等覆蓋）。"""
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    chunk = data.get("urls") or []
    if not job_id or not isinstance(chunk, list):
        return jsonify({"status": "failed", "error": "缺少 job_id 或 urls"}), 400
    # 金鑰不在 body：使用者金鑰從 access-controlled 的 job doc 讀回；無則回退系統
    # GENAI_API_KEY（env/Secret Manager，永不入佇列）。
    _jdoc = db.collection(JOBS_COLLECTION).document(job_id).get()
    _jd = _jdoc.to_dict() if _jdoc.exists else {}
    gemini_api_key = _jd.get("gemini_api_key") or os.environ.get("GENAI_API_KEY")
    from crawl_job import run_crawl_chunk
    try:
        run_crawl_chunk(
            job_id, chunk,
            int(data.get("chunk_index", 0)), int(data.get("n_chunks", 1)),
            int(data.get("offset", 0)),
            bool(data.get("use_gemini", False)),
            gemini_api_key,
            db,
            bool(data.get("force_listing", False)),
        )
    except Exception as e:
        print(f"[CrawlRun] 塊處理失敗（將由 Cloud Tasks 重試）: {e}", flush=True)
        return jsonify({"status": "failed", "error": str(e)}), 500
    return jsonify({"status": "ok"}), 200


@app.route("/api/crawl/<job_id>", methods=["GET"])
@require_api_key
def get_crawl_job(job_id):
    """查詢非同步爬取任務進度與結果。"""
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    try:
        doc = db.collection(JOBS_COLLECTION).document(job_id).get()
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Firestore 查詢失敗: {e}"}), 500
    if not doc.exists:
        return jsonify({"status": "failed", "error": f"找不到 job_id: {job_id}"}), 404
    data = doc.to_dict()
    # 結果存於 results 子集合（避免單文件 1MB 上限）；組裝回傳。後備：舊格式內嵌 results。
    try:
        sub = (db.collection(JOBS_COLLECTION).document(job_id)
               .collection("results").order_by("__name__").stream())
        results = [r.to_dict() for r in sub]
        if results:
            data["results"] = results
        else:
            data.setdefault("results", [])
    except Exception as e:
        print(f"[Crawler] 讀取 results 子集合失敗: {e}", flush=True)
        data.setdefault("results", [])
    return jsonify(data), 200


def _run_research_job(job_id: str, urls: list):
    """背景執行：選擇器研究 agent，結果寫 research_jobs/{job_id}。"""
    def _update(**fields):
        try:
            db.collection(RESEARCH_JOBS).document(job_id).update(
                {**fields, "updated_at": firestore.SERVER_TIMESTAMP})
        except Exception as e:
            print(f"[Research] job 更新失敗: {e}", flush=True)

    def _log(msg: str):
        print(msg, flush=True)
        _update(log=msg)

    try:
        from research import run_research
        _update(status="running", log="研究啟動...")
        out = run_research(urls, _log)
        _update(status="completed", result=out, progress=100,
                log=(f"完成：{len(out.get('candidates', []))} 個候選、"
                     f"{len(out.get('diagnoses', []))} 個診斷"),
                completed_at=firestore.SERVER_TIMESTAMP)
    except Exception as e:
        print(f"[Research] 研究任務失敗: {e}", flush=True)
        _update(status="failed", log=f"研究失敗：{e}")


@app.route("/api/research", methods=["POST"])
@require_api_key
def research():
    """非同步「選擇器研究」：對失敗 URL 依網域研究、產出候選選擇器 + 失敗診斷。
    on-demand、低頻、用戶觸發、與爬蟲不並行。

    Request: {"urls": [...]}（通常是爬取失敗的 URL）
    Response: {"job_id": "...", "status": "pending"}
    """
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    _reap()  # reap-on-submit
    data = request.get_json(silent=True) or {}
    urls = data.get("urls") or []
    if not isinstance(urls, list) or not urls:
        return jsonify({"status": "failed", "error": "Missing or empty 'urls' list"}), 400
    safe_urls, blocked = [], []
    for u in urls:
        ok, reason = _is_safe_url((u or "").strip())
        (safe_urls if ok else blocked).append(u if ok else {"url": u, "reason": reason})
    if not safe_urls:
        return jsonify({"status": "failed", "error": "無合法 URL（全被 SSRF 過濾）",
                        "blocked": blocked}), 400

    job_id = str(uuid.uuid4())
    import task_queue
    use_queue = task_queue.tasks_enabled()
    db.collection(RESEARCH_JOBS).document(job_id).set({
        "job_id": job_id, "status": "queued" if use_queue else "pending", "progress": 0,
        "log": "研究任務已建立...", "n_urls": len(safe_urls),
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP, "completed_at": None,
    })
    # 研究單次 ≤10 網域×120s < 派送上限 → 一個任務即可，不分塊。
    if use_queue and task_queue.enqueue("/api/research/run",
                                        {"job_id": job_id, "urls": safe_urls}):
        pass
    else:
        threading.Thread(target=_run_research_job, args=(job_id, safe_urls), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "queued" if use_queue else "pending"}), 202


@app.route("/api/research/run", methods=["POST"])
@require_api_key
def research_run():
    """Cloud Tasks worker：同步跑選擇器研究（請求生命週期內，concurrency=1）。"""
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    urls = data.get("urls") or []
    if not job_id or not isinstance(urls, list):
        return jsonify({"status": "failed", "error": "缺少 job_id 或 urls"}), 400
    try:
        _run_research_job(job_id, urls)
    except Exception as e:
        print(f"[ResearchRun] 失敗（將重試）: {e}", flush=True)
        return jsonify({"status": "failed", "error": str(e)}), 500
    return jsonify({"status": "ok"}), 200


@app.route("/api/research/<job_id>", methods=["GET"])
@require_api_key
def get_research_job(job_id):
    """查詢研究任務進度與結果（candidates / diagnoses）。"""
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    try:
        doc = db.collection(RESEARCH_JOBS).document(job_id).get()
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Firestore 查詢失敗: {e}"}), 500
    if not doc.exists:
        return jsonify({"status": "failed", "error": f"找不到 job_id: {job_id}"}), 404
    return jsonify(doc.to_dict()), 200


# ── 階段①：主文大圖擷取（只取圖、不碰文字；獨立端點，不在文字爬取流程內）──

@app.route("/api/extract-images", methods=["POST"])
@require_api_key
def extract_images():
    """非同步「主文大圖擷取」：對每個 URL 解析主文容器、蒐集容器內大圖
    （靜態優先、Chrome 補位）。與文字爬蟲嚴格分離、低頻 on-demand。

    Request: {"urls": [...]}
    Response: {"job_id": "...", "status": "pending"}
    """
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    _reap()  # reap-on-submit
    data = request.get_json(silent=True) or {}
    urls = data.get("urls") or []
    if not isinstance(urls, list) or not urls:
        return jsonify({"status": "failed", "error": "Missing or empty 'urls' list"}), 400
    if len(urls) > 1000:
        return jsonify({"status": "failed", "error": "Maximum 1000 URLs per job"}), 400
    safe_urls, blocked = [], []
    for u in urls:
        ok, reason = _is_safe_url((u or "").strip())
        (safe_urls if ok else blocked).append(u if ok else {"url": u, "reason": reason})
    # 逐 URL 跳過：只有「全部被擋」才整批失敗；否則照處理安全的、把被擋的記在 job 上。
    if not safe_urls:
        return jsonify({"status": "failed",
                        "error": f"全部 {len(blocked)} 個 URL 被 SSRF 過濾拒絕（無可處理）",
                        "blocked": blocked}), 400

    job_id = str(uuid.uuid4())
    from image_extract import chunk_image_urls, run_image_extract_batch
    import task_queue
    chunks = chunk_image_urls(safe_urls)
    use_queue = task_queue.tasks_enabled()
    db.collection(IMAGE_JOBS).document(job_id).set({
        "job_id": job_id, "status": "queued" if use_queue else "pending", "progress": 0,
        "log": f"影像擷取任務已建立...{f'（{len(blocked)} 個 URL 被安全過濾跳過）' if blocked else ''}",
        "total": len(safe_urls),
        "n_chunks": len(chunks), "chunks_done": {},
        "blocked": blocked, "n_blocked": len(blocked),
        "done": 0, "n_images": 0,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP, "completed_at": None,
    })
    if use_queue:
        enq_ok = 0
        for ci, offset, chunk in chunks:
            if task_queue.enqueue("/api/extract-images/run", {
                "job_id": job_id, "urls": chunk, "chunk_index": ci,
                "n_chunks": len(chunks), "offset": offset,
            }):
                enq_ok += 1
        if enq_ok < len(chunks):
            db.collection(IMAGE_JOBS).document(job_id).update({
                "status": "failed", "log": f"入列失敗（{enq_ok}/{len(chunks)} 塊）",
                "updated_at": firestore.SERVER_TIMESTAMP})
            return jsonify({"status": "failed",
                            "error": f"Cloud Tasks 入列失敗（{enq_ok}/{len(chunks)}）"}), 502
    else:
        threading.Thread(target=run_image_extract_batch,
                         args=(job_id, safe_urls, db), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "queued" if use_queue else "pending"}), 202


@app.route("/api/extract-images/run", methods=["POST"])
@require_api_key
def extract_images_run():
    """Cloud Tasks worker：同步擷取單一塊的大圖。"""
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    chunk = data.get("urls") or []
    if not job_id or not isinstance(chunk, list):
        return jsonify({"status": "failed", "error": "缺少 job_id 或 urls"}), 400
    from image_extract import run_image_extract_chunk
    try:
        run_image_extract_chunk(job_id, chunk, int(data.get("chunk_index", 0)),
                                int(data.get("n_chunks", 1)), int(data.get("offset", 0)), db)
    except Exception as e:
        print(f"[ExtractRun] 塊處理失敗（將重試）: {e}", flush=True)
        return jsonify({"status": "failed", "error": str(e)}), 500
    return jsonify({"status": "ok"}), 200


@app.route("/api/extract-images/<job_id>", methods=["GET"])
@require_api_key
def get_extract_images_job(job_id):
    """查詢影像擷取任務進度與結果（results 子集合）。"""
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    try:
        doc = db.collection(IMAGE_JOBS).document(job_id).get()
    except Exception as e:
        return jsonify({"status": "failed", "error": f"Firestore 查詢失敗: {e}"}), 500
    if not doc.exists:
        return jsonify({"status": "failed", "error": f"找不到 job_id: {job_id}"}), 404
    data = doc.to_dict()
    try:
        sub = (db.collection(IMAGE_JOBS).document(job_id)
               .collection("results").order_by("__name__").stream())
        results = [r.to_dict() for r in sub]
        data["results"] = results
    except Exception as e:
        print(f"[ImageExtract] 讀取 results 子集合失敗: {e}", flush=True)
        data.setdefault("results", [])
    return jsonify(data), 200


@app.route("/api/crawl/<job_id>/cancel", methods=["POST"])
@require_api_key
def cancel_crawl_job(job_id):
    """請求取消非同步爬取任務（合作式）。

    設 cancel_requested=True；背景迴圈於每篇前檢查，收到即停止並轉 cancelled。
    若任務已完成/失敗則不影響。回傳當前狀態。
    """
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    try:
        ref = db.collection(JOBS_COLLECTION).document(job_id)
        doc = ref.get()
        if not doc.exists:
            return jsonify({"status": "failed", "error": f"找不到 job_id: {job_id}"}), 404
        cur = doc.to_dict().get("status")
        if cur in ("completed", "failed", "cancelled"):
            return jsonify({"status": cur, "message": "任務已結束，無需取消"}), 200
        ref.update({"cancel_requested": True,
                    "updated_at": firestore.SERVER_TIMESTAMP})
        return jsonify({"status": "cancelling", "job_id": job_id}), 200
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e)}), 500


@app.route("/api/crawl/cleanup", methods=["POST"])
@require_api_key
def cleanup_crawl_jobs():
    """清除孤兒/陳舊爬取任務文件（status 已結束且超過 days 天）。

    Request: {"days": 7}（預設 7）。回傳刪除筆數。
    crawl_jobs 是 crawler 的暫存層，結果回收進 content-analyser 後即可清理。
    """
    if db is None:
        return jsonify({"status": "failed", "error": "Firestore 未連線"}), 503
    reaped = _reap()  # 先收割卡住的非終態任務（標 failed）→ 下方再刪已結束的
    import datetime
    data = request.get_json(silent=True) or {}
    try:
        days = max(0, int(data.get("days", 7)))
    except (TypeError, ValueError):
        days = 7
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    deleted = 0
    MAX_CLEAN = 500  # 單次上限，避免一次掃/刪過多導致請求逾時
    try:
        # 清 crawler 自管的 3 個 job 集合（crawl / image_extract / research）——之前只清 crawl_jobs，
        # 導致已完成的圖片擷取/研究記錄（純文字網址清單）永遠累積。三個都清才不會慢慢長大。
        # 只查「已結束」狀態（伺服器端單欄位過濾，免複合索引）+ 每集合上限；cutoff 比較在取回後做。
        for col in _REAP_COLLECTIONS:
            q = (db.collection(col)
                 .where("status", "in", ["completed", "failed", "cancelled"])
                 .limit(MAX_CLEAN))
            for doc in q.stream():
                d = doc.to_dict() or {}
                updated = d.get("updated_at") or d.get("completed_at")
                if updated is None or updated < cutoff:
                    try:
                        rbatch = db.batch()
                        nr = 0
                        for r in doc.reference.collection("results").stream():
                            rbatch.delete(r.reference)
                            nr += 1
                            if nr % 400 == 0:
                                rbatch.commit()
                                rbatch = db.batch()
                        rbatch.commit()
                    except Exception as e:
                        print(f"[cleanup] 刪除 results 子集合失敗 {doc.id}: {e}", flush=True)
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
