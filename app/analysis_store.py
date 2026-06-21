# -*- coding: utf-8 -*-
"""分析狀態對帳層（自 project_routes.py 抽出）。

單一職責：把 analysis-pipeline 服務回報的 job 狀態「對帳」寫回 Firestore analyses doc
（主分析 + 延伸報告），供輪詢端與報告頁載入（lazy 自癒）共用。核心規則：只把 server 真實
終態（completed/failed/cancelled）+ not_found 當終態寫回；傳輸層 unavailable / 進行中不寫
（杜絕「仍在跑的 job 被誤標 failed / 結果永不寫回」兩類根因）。

依賴 services.db、firestore、analysis_client。不依賴 project_routes（無循環）。
"""
from firebase_admin import firestore

from .services import db
from .analysis_client import (
    get_job_status, get_image_analysis_status, get_combined_status, get_audience_status,
)


def _analysis_ref(pid, aid):
    return db.collection('projects').document(pid).collection('analyses').document(aid)


def _reconcile_analysis(pid: str, aid: str, analysis: dict) -> dict:
    """對帳主分析 job（含 visual/combined）：只把 server 的 completed/failed/cancelled 當終態寫回；
    not_found→failed（job 遺失）；unavailable（傳輸層暫時失敗）或進行中→不寫終態、保持原狀。
    供輪詢端與報告頁載入（lazy 自癒）共用，避免「job 跑超過前端輪詢時限/離開頁面 → 結果永不寫回」。
    就地更新傳入的 analysis dict。回 {status, progress, log}。"""
    status = analysis.get('status', 'pending')
    job_id = analysis.get('job_id')
    if status not in ('pending', 'running') or not job_id:
        return {'status': status,
                'progress': analysis.get('progress', 100 if status == 'completed' else 0),
                'log': analysis.get('log', '')}

    kind = analysis.get('kind')
    if kind == 'visual':
        ps = get_image_analysis_status(job_id)
    elif kind == 'combined':
        ps = get_combined_status(job_id)
    else:
        ps = get_job_status(job_id)
    new = ps.get('status', status)
    progress = ps.get('progress', analysis.get('progress', 0))
    plog = ps.get('log', analysis.get('log', ''))

    # 傳輸層暫時失敗 → 保持原狀，不寫（這正是先前把仍在跑的 job 誤標 failed/凍結的根因）
    if new == 'unavailable':
        return {'status': status, 'progress': analysis.get('progress', 0),
                'log': analysis.get('log', '')}

    ref = (db.collection('projects').document(pid)
           .collection('analyses').document(aid))
    update = {'updated_at': firestore.SERVER_TIMESTAMP}

    if new == 'completed':
        update.update({'status': 'completed', 'progress': 100, 'log': plog,
                       'result_markdown': ps.get('result_markdown', ''),
                       'completed_at': firestore.SERVER_TIMESTAMP})
        ne = ps.get('numeric_exports')
        if isinstance(ne, dict) and ne:
            update['numeric_exports'] = ne
        # Token 記帳（用戶付，跟專案走）：存該分析的 token_usage + 累加專案總額。
        # 完成分支（line 638 守衛）每個分析僅進一次，故 Increment 不會重複累加。
        tu = ps.get('token_usage')
        if isinstance(tu, dict) and (tu.get('totals') or {}).get('total'):
            update['token_usage'] = tu
            try:
                db.collection('projects').document(pid).update(
                    {'token_usage_total': firestore.Increment(int(tu['totals']['total']))})
            except Exception as e:
                print(f"[token] 專案總額累加略過：{e}", flush=True)
        ref.update(update); analysis.update(update)
        return {'status': 'completed', 'progress': 100, 'log': plog}

    if new in ('failed', 'cancelled', 'not_found'):
        st = 'failed' if new == 'not_found' else new
        err = (plog if new == 'cancelled'
               else ps.get('error', plog) or ('任務遺失' if new == 'not_found' else plog))
        update.update({'status': st, 'log': err, 'completed_at': firestore.SERVER_TIMESTAMP})
        ref.update(update); analysis.update(update)
        return {'status': st, 'progress': progress, 'log': err}

    # 仍 running/pending → 更新進度（非終態）
    update.update({'status': new if new in ('pending', 'running') else status,
                   'progress': progress, 'log': plog})
    ref.update(update); analysis.update(update)
    return {'status': update['status'], 'progress': progress, 'log': plog}


def _reconcile_derive(pid, aid, analysis: dict) -> dict:
    """就地對帳延伸報告：若卡 running 但 job 已完成/失敗，補寫 analyses doc。
    回傳 {status, log?, error?}。供前端輪詢與報告頁載入（lazy 自癒）共用，
    避免「job 跑超過前端輪詢時限 → 結果永不寫回」的卡死。"""
    dstatus = analysis.get('derive_status')
    job_id = analysis.get('derive_job_id')
    if dstatus != 'running' or not job_id:
        return {'status': dstatus or 'none'}
    ps = get_audience_status(job_id)
    new = ps.get('status', dstatus)
    # 傳輸層暫時失敗（unavailable）→ 保持 running、不寫終態（先前把一次逾時/503 誤判成永久失敗的根因）
    if new == 'unavailable':
        return {'status': 'running', 'log': ps.get('log', '')}
    if new == 'completed':
        reports = ps.get('audience_reports') or {}
        dupd = {'derived_reports': reports, 'derive_status': 'completed'}
        # 延伸報告 token（用戶付）→ 存母分析 derive_token_usage（不覆蓋主 token_usage）+ 累加專案總額。
        # 完成分支（line 873 守衛）每分析僅進一次，Increment 不重複。
        tu = ps.get('token_usage')
        if isinstance(tu, dict) and (tu.get('totals') or {}).get('total'):
            dupd['derive_token_usage'] = tu
            try:
                db.collection('projects').document(pid).update(
                    {'token_usage_total': firestore.Increment(int(tu['totals']['total']))})
            except Exception as e:
                print(f"[token] 延伸報告專案總額累加略過：{e}", flush=True)
        _analysis_ref(pid, aid).update(dupd)
        analysis['derived_reports'] = reports
        analysis['derive_status'] = 'completed'
        return {'status': 'completed'}
    # 只有 server 真實終態（failed/cancelled）或 job 遺失（not_found）才標失敗
    if new in ('failed', 'cancelled', 'not_found'):
        err = (ps.get('error', ps.get('log', '產生失敗'))
               if new != 'not_found' else '延伸報告任務遺失')
        _analysis_ref(pid, aid).update({
            'derive_status': 'failed', 'derive_error': err})
        analysis['derive_status'] = 'failed'
        analysis['derive_error'] = err
        return {'status': 'failed', 'error': err}
    return {'status': 'running', 'log': ps.get('log', '')}


def _derived_label(analysis, slug):
    """從 derive_experts 取該 slug 的顯示名（找不到就回 slug）。"""
    for e in (analysis.get('derive_experts') or []):
        if e.get('slug') == slug:
            return e.get('label', slug)
    return slug
