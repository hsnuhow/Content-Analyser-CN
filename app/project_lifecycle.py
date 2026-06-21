# -*- coding: utf-8 -*-
"""專案生命週期層（自 project_routes.py 抽出）。

單一職責：列出專案內執行中的相依工作、以及串接刪除（cascade）整個專案（含 datasets /
analyses / items 子集合；強制刪除時先取消執行中的爬蟲/分析 job）。

依賴 services.db、crawler_client、analysis_client、datasets_store。不依賴 project_routes（無循環）。
"""
from .services import db
from .crawler_client import cancel_crawl
from .analysis_client import cancel_analysis
from .datasets_store import _delete_dataset_items


def _project_active_jobs(pid: str) -> list:
    """列出專案內執行中的相依工作（dataset crawling / analysis pending|running）。"""
    base = db.collection('projects').document(pid)
    active = []
    for d in base.collection('datasets').stream():
        data = d.to_dict() or {}
        if data.get('status') == 'crawling':
            active.append(('資料集', data.get('name', d.id), data.get('crawl_job_id')))
    for a in base.collection('analyses').stream():
        data = a.to_dict() or {}
        if data.get('status') in ('pending', 'running'):
            active.append(('分析', data.get('report_title', a.id), data.get('job_id')))
    return active


def _cascade_delete_project(pid: str, cancel: bool = False) -> None:
    """刪除專案及其所有 datasets / analyses。cancel=True 時先取消執行中工作（強制刪除用）。"""
    base = db.collection('projects').document(pid)
    for d in base.collection('datasets').stream():
        data = d.to_dict() or {}
        if cancel and data.get('status') == 'crawling' and data.get('crawl_job_id'):
            try:
                cancel_crawl(data['crawl_job_id'])
            except Exception:
                pass
        _delete_dataset_items(pid, d.id)  # 連同 items 子集合
        d.reference.delete()
    for a in base.collection('analyses').stream():
        data = a.to_dict() or {}
        if cancel and data.get('status') in ('pending', 'running') and data.get('job_id'):
            try:
                cancel_analysis(data['job_id'])
            except Exception:
                pass
        a.reference.delete()
    base.delete()
