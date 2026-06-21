# -*- coding: utf-8 -*-
"""搜尋情報領域路由（探勘）：內容發現（discover）+ 品牌聲量探勘（brand-presence）
+ 推薦筆記/品牌掃描的管理與轉草稿。

註冊在 project_bp（package __init__ 於底部 import 本模組以套用 @bp.route）。
"""
from flask import request, jsonify, flash, redirect, url_for
from firebase_admin import firestore

from . import bp, project_access_required, current_user_email
from ..services import db
from ..datasets_store import _save_dataset_items, _append_urls_to_draft
from ..search_extent_client import (discover as _discover, brand_presence as _bp,
                                     is_configured)


@bp.route('/<pid>/discover', methods=['POST'])
@project_access_required(min_role='editor')
def discover_urls(pid, project, role):
    """搜尋情報·內容發現（爬蟲前置）：關鍵字 → 推薦爬取 URL 清單（呼叫 search-extent）。
    結果**持久化**為「推薦筆記」（discoveries 子集合），供之後回來勾選建/併草稿。
    回 {ok, discovery_id} 供前端 reload 顯示。"""
    q = (request.form.get('q') or request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': '缺少關鍵字'}), 400
    if not is_configured():
        return jsonify({'error': '搜尋情報服務尚未接上（SEARCH_EXTENT 未設定）。'}), 503
    res = _discover(q, max_results=50)
    if res.get('error'):
        return jsonify(res), 502
    cands = res.get('candidates') or []
    if not cands:
        return jsonify({'error': f'「{q}」沒有找到推薦結果。'}), 200
    ref = (db.collection('projects').document(pid)
           .collection('discoveries').document())
    ref.set({
        'id': ref.id, 'query': q, 'candidates': cands,
        'count': len(cands), 'by_source': res.get('by_source', {}),
        'created_by': current_user_email(),
        'created_at': firestore.SERVER_TIMESTAMP,
    })
    return jsonify({'ok': True, 'discovery_id': ref.id, 'count': len(cands)})


@bp.route('/<pid>/brand-presence', methods=['POST'])
@project_access_required(min_role='editor')
def brand_presence_run(pid, project, role):
    """品牌聲量探勘：主題 + 品牌清單 → 各品牌 earned 聲量；結果存 brand_scans。"""
    topic = (request.form.get('topic') or '').strip()
    brands = [b.strip() for b in (request.form.get('brands') or '').splitlines() if b.strip()]
    if not topic or not brands:
        return jsonify({'error': '請填主題與至少一個品牌'}), 400
    if not is_configured():
        return jsonify({'error': '搜尋情報服務尚未接上（SEARCH_EXTENT 未設定）。'}), 503
    res = _bp(topic, brands[:30])
    if res.get('error'):
        return jsonify(res), 502
    results = res.get('results') or []
    ref = (db.collection('projects').document(pid)
           .collection('brand_scans').document())
    ref.set({'id': ref.id, 'topic': topic, 'count': len(results), 'results': results,
             'created_by': current_user_email(),
             'created_at': firestore.SERVER_TIMESTAMP})
    return jsonify({'ok': True, 'scan_id': ref.id, 'count': len(results)})


@bp.route('/<pid>/brand-scans/<sid>/delete', methods=['POST'])
@project_access_required(min_role='editor')
def delete_brand_scan(pid, sid, project, role):
    try:
        (db.collection('projects').document(pid)
         .collection('brand_scans').document(sid).delete())
        flash('已刪除品牌聲量探勘紀錄。', 'success')
    except Exception as e:
        flash(f'刪除失敗：{e}', 'danger')
    return redirect(url_for('project_bp.project_detail', pid=pid))


@bp.route('/<pid>/discoveries/<did>/delete', methods=['POST'])
@project_access_required(min_role='editor')
def delete_discovery(pid, did, project, role):
    """刪除一則推薦筆記。"""
    try:
        (db.collection('projects').document(pid)
         .collection('discoveries').document(did).delete())
        flash('已刪除推薦筆記。', 'success')
    except Exception as e:
        flash(f'刪除失敗：{e}', 'danger')
    return redirect(url_for('project_bp.project_detail', pid=pid))


@bp.route('/<pid>/discoveries/<did>/to-draft', methods=['POST'])
@project_access_required(min_role='editor')
def discovery_to_draft(pid, did, project, role):
    """把推薦筆記勾選的 URL → 建立新草稿 或 併入現有草稿。"""
    urls = [u.strip() for u in request.form.getlist('urls') if u.strip()]
    urls = list(dict.fromkeys(urls))  # 去重保序
    if not urls:
        flash('請至少勾選一個結果。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    mode = request.form.get('mode', 'new')        # 'new' 建新草稿 / 'append' 併入現有
    if mode == 'new':
        name = (request.form.get('name') or '').strip() or '推薦清單'
        ds_ref = (db.collection('projects').document(pid)
                  .collection('datasets').document())
        ds_ref.set({
            'id': ds_ref.id, 'name': name, 'source_urls': urls,
            'crawl_job_id': None, 'status': 'draft', 'use_gemini': False,
            'progress': 0, 'log': '由推薦筆記建立的草稿清單。',
            'item_count': len(urls), 'created_by': current_user_email(),
            'created_at': firestore.SERVER_TIMESTAMP,
            'updated_at': firestore.SERVER_TIMESTAMP,
        })
        _save_dataset_items(pid, ds_ref.id, [{'url': u, 'status': 'pending'} for u in urls])
        flash(f'已建立草稿資料集「{name}」（{len(urls)} 個網址）。', 'success')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=ds_ref.id))
    # 併入現有草稿
    existing_did = request.form.get('existing_did', '')
    added = _append_urls_to_draft(pid, existing_did, urls)
    if added is None:
        flash('目標草稿不存在或已非草稿狀態。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    flash(f'已加入現有草稿（新增 {added} 個、去重略過 {len(urls) - added} 個）。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=existing_did))
