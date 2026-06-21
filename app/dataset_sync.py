# -*- coding: utf-8 -*-
"""爬取狀態同步層（自 project_routes.py 抽出）。

單一職責：當 dataset 仍在 crawling 時，向 content-crawler 拉 job 最新狀態並同步回 Firestore
（後端主動同步 + lazy 自癒，避免「使用者離開頁面 → crawler 背景跑完 → 結果永不回收、永遠卡
crawling」）。含交易式「自動續批」認領（防多 poller/多分頁重複 spawn）。

依賴 services.db、firestore、crawler_client、datasets_store。不依賴 project_routes（無循環）。
"""
from firebase_admin import firestore

from .services import db
from .crawler_client import submit_crawl_batch, get_crawl_status
from .datasets_store import _load_dataset_items, _save_dataset_items, _replace_items_by_url

AUTO_CONTINUE_MAX_ROUNDS = 15   # 自動續批最多輪數（補爬未爬項，防無限續批）


def _claim_auto_continue(ds_ref, job_id: str) -> bool:
    """交易式認領「為此已完成 job 送出下一輪續批」的權利，防多 poller/多分頁重複 spawn。
    僅當 dataset 仍 crawling、crawl_job_id 仍等於 job_id、且尚未被認領時成功（回 True）。"""
    @firestore.transactional
    def _claim(t):
        snap = ds_ref.get(transaction=t)
        d = snap.to_dict() or {}
        if d.get('status') != 'crawling' or d.get('crawl_job_id') != job_id:
            return False
        if d.get('_continue_claimed_for') == job_id:
            return False
        t.update(ds_ref, {'_continue_claimed_for': job_id})
        return True
    try:
        return bool(_claim(db.transaction()))
    except Exception as e:
        print(f"[sync] 續批認領交易失敗，保守跳過：{e}", flush=True)
        return False


def _sync_crawling_dataset(pid: str, did: str, dataset: dict = None,
                           allow_spawn: bool = False):
    """若 dataset 仍在 crawling，向 crawler 拉 job 最新狀態並同步回 Firestore。
    回傳最新 dataset dict（含 id）；找不到回傳 None。

    這是「後端主動同步」的核心：頁面載入時呼叫（dataset_detail / project_detail），
    確保即使使用者離開頁面、crawler 在背景跑完，下次進頁面就會回收結果並轉 completed，
    不再只依賴前端輪詢（離開即斷 → 永遠卡 crawling）。

    allow_spawn：是否允許「自動續批」實際送出新爬蟲批次（有副作用、耗 Owner 配額）。
      預設 False → 任何角色（含 Viewer）都能同步/回收結果，但只有 editor+ 的呼叫端
      （傳 allow_spawn=True）能觸發續批，避免 Viewer 的唯讀輪詢產生爬蟲副作用。
    """
    ds_ref = (db.collection('projects').document(pid)
              .collection('datasets').document(did))
    if dataset is None:
        doc = ds_ref.get()
        if not doc.exists:
            return None
        dataset = doc.to_dict() | {'id': did}

    if dataset.get('status') != 'crawling':
        return dataset

    # ① lazy 自癒：dataset 卡在 crawling 過久（crawler job 已死/被回收且沒人輪詢）→ 標 failed，
    #   不再永遠轉圈。閾值 90 分 > crawler 端 reaper（60 分）+ 批次時限，讓 crawler 端先處理。
    import datetime as _dt
    _upd = dataset.get('updated_at')
    try:
        if _upd is not None:
            _age_min = (_dt.datetime.now(_dt.timezone.utc) - _upd).total_seconds() / 60
            if _age_min > 90:
                ds_ref.update({'status': 'failed',
                               'log': '逾時自癒：超過 90 分無進度，疑爬取中止（reaped）',
                               'updated_at': firestore.SERVER_TIMESTAMP})
                return {**dataset, 'status': 'failed'}
    except Exception:
        pass

    job_id = dataset.get('crawl_job_id')
    if not job_id:
        return dataset

    job = get_crawl_status(job_id)
    if not isinstance(job, dict):
        return dataset
    jstatus = job.get('status', 'crawling')
    # 傳輸層暫時失敗（逾時/連線/503）→ 保持 crawling、不動（先前一次網路抖動就把仍在跑的
    #   資料集誤標 failed 的根因）。crawler 端 90 分逾時自癒（上方①）會處理真正卡死。
    if jstatus == 'unavailable':
        return dataset
    if jstatus == 'not_found':  # crawler 明確回 404（job 已刪/遺失）→ 視為失敗，解除 crawling 卡死
        ds_ref.update({'status': 'failed',
                       'log': job.get('error', '爬取任務遺失'),
                       'updated_at': firestore.SERVER_TIMESTAMP})
        return {**dataset, 'status': 'failed'}
    update = {
        'progress': job.get('progress', dataset.get('progress', 0)),
        'log': job.get('log', dataset.get('log', '')),
        'updated_at': firestore.SERVER_TIMESTAMP,
    }
    # 被 SSRF 安全過濾擋下的 URL（含 reason）→ 存到 dataset 供前端顯示「哪幾個、為什麼沒爬」。
    if job.get('n_blocked'):
        update['blocked'] = job.get('blocked', [])
        update['n_blocked'] = job.get('n_blocked', 0)
    if jstatus == 'completed':
        results = job.get('results', []) or []
        # 寫入 items 子集合：recrawl/續批只替換指定 url（保留已成功項）；否則整批寫入。
        recrawl_urls = dataset.get('recrawl_urls')
        if recrawl_urls:
            _replace_items_by_url(pid, did, set(recrawl_urls), results)
        else:
            _save_dataset_items(pid, did, results, append=False)
        items = _load_dataset_items(pid, did)

        # ── 自動續批：把「未爬取（被時限/連續卡死切掉，unattempted）」的項自動再開一批爬完，
        #    直到沒有未爬項或達上限。真失敗（403/卡死）不自動重試。 ──
        unattempted = list(dict.fromkeys(
            it.get('url') for it in items if it.get('unattempted') and it.get('url')))
        auto_round = int(dataset.get('auto_round', 0) or 0)
        # 只有 editor+ 的呼叫端（allow_spawn）能觸發續批；且用交易「認領」此 job_id 的續批，
        # 防止多分頁/多 poller 在同一完成時刻各自送出一批（雙開重複 spawn）。
        if (allow_spawn and unattempted and auto_round < AUTO_CONTINUE_MAX_ROUNDS
                and _claim_auto_continue(ds_ref, job_id)):
            proj = db.collection('projects').document(pid).get()
            lc = (proj.to_dict() or {}).get('llm_config', {}) if proj.exists else {}
            gkey = lc.get('api_key') if lc.get('provider') == 'gemini' else None
            res = submit_crawl_batch(unattempted, use_gemini=bool(gkey), gemini_api_key=gkey)
            if 'error' not in res and res.get('job_id'):
                update['status'] = 'crawling'
                update['crawl_job_id'] = res['job_id']
                update['recrawl_urls'] = unattempted
                update['auto_round'] = auto_round + 1
                update['progress'] = 0
                update['log'] = (f'自動續批（第 {auto_round + 1} 輪）：補爬 '
                                 f'{len(unattempted)} 個未爬項...')
                ds_ref.update(update)
                return {**dataset, **update}

        # 沒有未爬項 / submit 失敗 / 達上限 → 視為完成
        update['status'] = 'completed'
        update['item_count'] = len(items)
        update['succeeded'] = sum(1 for it in items if it.get('status') == 'success')
        # 完成時「權威重寫」被 SSRF 擋下的名單：依最新 job 結果（沒有就歸零）。
        # 被擋的 URL 一律是非成功項、必落在 failed/all 重爬範圍，故最新 job 的 n_blocked 即現況；
        # 不重寫的話舊的封鎖橫幅永遠掛著、無法關閉（即使該 URL 已修好重爬成功）。
        update['blocked'] = job.get('blocked', []) or []
        update['n_blocked'] = job.get('n_blocked', 0) or 0
        if recrawl_urls:
            update['recrawl_urls'] = firestore.DELETE_FIELD
        if dataset.get('auto_round'):
            update['auto_round'] = firestore.DELETE_FIELD

        # ── P1：爬完自動觸發選擇器研究（抽取品質差的尾巴：失敗 + 掉到整頁 body）──
        #   把「用戶手動點研究」改為「爬完自動對失敗尾巴跑」。每個 dataset 只觸發一次。
        #   只有 editor+（allow_spawn）能觸發：有副作用、耗系統 Gemini token + Chrome。
        #   failed 由 items 的 status 取（完整）；body_fallback 由本次 job results 的 resolved_by 取
        #   （resolved_by 不寫進 dataset items，故讀 raw results）。research 端另有 MAX_DOMAINS=10 上限。
        if allow_spawn and not dataset.get('_auto_research_done'):
            research_urls = [it.get('url') for it in items
                             if it.get('status') == 'failed' and it.get('url')]
            research_urls += [r.get('url') for r in results
                              if r.get('url') and r.get('resolved_by') == 'body_fallback']
            research_urls = list(dict.fromkeys(u for u in research_urls if u))
            if research_urls:
                try:
                    from .crawler_client import submit_research
                    rres = submit_research(research_urls[:50])
                    if isinstance(rres, dict) and rres.get('job_id'):
                        update['_auto_research_done'] = True
                        update['_auto_research_job'] = rres['job_id']
                        update['_auto_research_n'] = len(research_urls)
                        print(f"[sync] 自動研究觸發：{len(research_urls)} 個失敗尾巴 URL "
                              f"→ research job {rres['job_id']}", flush=True)
                except Exception as e:
                    print(f"[sync] 自動研究觸發略過：{e}", flush=True)
    elif jstatus == 'failed':
        update['status'] = 'failed'
        update['log'] = job.get('log', '爬取失敗')
    ds_ref.update(update)
    return {**dataset, **update}
