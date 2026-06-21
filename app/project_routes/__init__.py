# -*- coding: utf-8 -*-
"""
Project 與 Analysis 路由（package：__init__ 定義 bp + 共用 helper；
後續各領域路由可拆為 projects.py / analysis.py / datasets.py / discovery.py 子模組）

Blueprint：project_bp（prefix /projects）

路由：
  GET  /projects                   → 列出用戶參與的所有 Project
  GET  /projects/new               → 建立 Project 表單
  POST /projects                   → 建立 Project
  GET  /projects/<pid>             → Project 詳情（分析列表）
  POST /projects/<pid>/settings    → 更新 Project 設定（Owner）
  POST /projects/<pid>/members     → 新增成員（Owner）
  POST /projects/<pid>/members/remove → 移除成員（Owner）
  POST /projects/<pid>/analyses    → 提交分析任務（Owner/Editor）
  GET  /projects/<pid>/analyses/<aid>          → 查看報告
  GET  /projects/<pid>/analyses/<aid>/download → 下載 .md
  GET  /projects/<pid>/analyses/<aid>/status   → 輪詢進度（JSON）
"""
import json
import re
import requests
from functools import wraps
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, url_for, flash, send_file, abort)
from firebase_admin import firestore
from io import BytesIO

from ..services import db, get_admin_email
from ..auth_guards import login_required, refresh_whitelist_status
from ..analysis_client import (submit_analysis, get_job_status, cancel_analysis,
                              submit_image_analysis, get_image_analysis_status,
                              submit_combined, get_combined_status,
                              submit_audience, get_audience_status)
from ..crawler_client import (submit_crawl_batch, get_crawl_status, cancel_crawl,
                             submit_research, get_research_status,
                             submit_extract_images, get_extract_images_status)
from .. import kb_store
from ..dataset_sync import _sync_crawling_dataset  # project_detail 載入時同步 crawling 資料集

bp = Blueprint('project_bp', __name__, url_prefix='/projects')

# 自動續批最多輪數（每輪一個 ≤45 分批次補爬「未爬取」項），防失控。

# ──────────────────────────────────────────────────────────────────────
# 輔助函式
# ──────────────────────────────────────────────────────────────────────

def current_user_email() -> str:
    return session.get('user', {}).get('email', '')


# URL 工具與資料集 items store 層已抽出（見 url_utils.py / datasets_store.py）。
from ..url_utils import _TRACKING_PARAMS, _url_key, parse_url_list  # noqa: F401
from ..datasets_store import (  # noqa: F401  （re-export：admin_routes 仍 from project_routes import _load_dataset_items）
    _items_ref, _load_dataset_items, _save_dataset_items,
    _delete_dataset_items, _append_urls_to_draft, _replace_items_by_url,
)

def is_admin() -> bool:
    admin = get_admin_email()
    return bool(admin and current_user_email().lower() == admin.lower())


def get_project(pid: str) -> dict | None:
    """讀取 projects/{pid}，不存在回傳 None。"""
    doc = db.collection('projects').document(pid).get()
    return doc.to_dict() | {'id': pid} if doc.exists else None


def get_user_role(project: dict, email: str) -> str | None:
    """回傳用戶在 Project 中的角色：'owner' | 'editor' | 'viewer' | None。"""
    if not project:
        return None
    if is_admin():
        return 'owner'  # Admin 視為 Owner
    if project.get('owner', '').lower() == email.lower():
        return 'owner'
    members = project.get('members', {})
    return members.get(email.lower())


def log_usage(action: str, detail: str = '', count: int = 1,
              project_id: str = '', email: str = None):
    """記錄使用量事件至 users/{email}/usage_log/{auto_id}。

    action 例：'crawl'、'manual_import'、'analyse'、'delete_dataset'、'delete_analysis'。
    用量統計（按用戶）供 Admin 檢視。失敗只記 log，不影響主流程。
    """
    email = (email or current_user_email() or '').lower()
    if not email:
        return
    try:
        (db.collection('users').document(email)
         .collection('usage_log').document().set({
             'action': action,
             'detail': str(detail)[:200],
             'count': int(count) if isinstance(count, (int, float)) else 1,
             'project_id': project_id,
             'at': firestore.SERVER_TIMESTAMP,
         }))
    except Exception as e:
        print(f"[usage_log] 寫入失敗（{email}/{action}）：{e}", flush=True)


def project_access_required(min_role: str = 'viewer'):
    """確認用戶有 Project 存取權。min_role: 'viewer' | 'editor' | 'owner'。"""
    ROLE_LEVEL = {'viewer': 1, 'editor': 2, 'owner': 3}

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('main_bp.auth'))
            # 白名單 gate（帶 TTL 回查，撤銷及時生效）：非 approved（pending/rejected）
            # 不得訪問任何專案資源，即使其 email 被列為某專案 member。
            if refresh_whitelist_status() != 'approved':
                return redirect(url_for('main_bp.pending'))
            pid = kwargs.get('pid')
            project = get_project(pid)
            if not project:
                abort(404)
            role = get_user_role(project, current_user_email())
            if not role or ROLE_LEVEL.get(role, 0) < ROLE_LEVEL.get(min_role, 1):
                abort(403)
            # 封存 gate：封存後僅 Owner/Admin（role=='owner'）可進入，Editor/Viewer 擋下。
            if project.get('archived') and role != 'owner':
                flash('此專案已封存，僅 Owner 或管理員可存取。', 'warning')
                return redirect(url_for('project_bp.list_projects'))
            kwargs['project'] = project
            kwargs['role'] = role
            return f(*args, **kwargs)
        return decorated
    return decorator


# ──────────────────────────────────────────────────────────────────────
# Project 路由
# ──────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
def list_projects():
    email = current_user_email()
    admin = is_admin()

    projects = []
    seen_ids = set()

    if admin:
        # 管理員全站視角：仍需全掃（單一管理員，可接受；非熱路徑的多人頁面）。
        for d in db.collection('projects').stream():
            data = d.to_dict() | {'id': d.id}
            data['_foreign'] = (data.get('owner') != email
                                and email not in data.get('members', {}))
            projects.append(data)
            seen_ids.add(d.id)
    else:
        # 非管理員：兩個索引查詢，避免全表掃描（N+1 修正）。
        #   1) 我是 Owner 的（owner 等值查，永遠可靠、不依賴 member_emails）。
        #   2) 我是成員的（member_emails array_contains；與 members 同步維護，見 add/remove_member）。
        for d in db.collection('projects').where('owner', '==', email).stream():
            projects.append(d.to_dict() | {'id': d.id})
            seen_ids.add(d.id)
        for d in db.collection('projects').where('member_emails', 'array_contains', email).stream():
            if d.id in seen_ids:
                continue
            projects.append(d.to_dict() | {'id': d.id})
            seen_ids.add(d.id)

    # 按建立時間排序；封存的排到最後（穩定排序，仍灰階顯示於同一列表）
    projects.sort(key=lambda p: p.get('created_at') or '', reverse=True)
    projects.sort(key=lambda p: 1 if p.get('archived') else 0)
    return render_template('projects.html', projects=projects, is_admin=admin)


@bp.route('/new', methods=['GET'])
@login_required
def new_project():
    return render_template('project_new.html')


@bp.route('/', methods=['POST'])
@login_required
def create_project():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    if not title:
        flash('請填寫專案名稱。', 'danger')
        return redirect(url_for('project_bp.new_project'))

    email = current_user_email()
    ref = db.collection('projects').document()
    ref.set({
        'title': title,
        'description': description,
        'owner': email,
        'members': {},
        'member_emails': [],   # N+1 修正：成員 email 陣列（供 list_projects 用 array_contains 索引查，免全掃）
        'llm_config': {
            'provider': 'gemini',
            'model': 'gemini-2.5-flash',
            'api_key': '',
        },
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    flash(f'專案「{title}」建立成功！請設定 LLM Key 後才能提交分析。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=ref.id))


@bp.route('/<pid>')
@project_access_required(min_role='viewer')
def project_detail(pid, project, role):
    # 載入分析列表
    analyses_docs = (
        db.collection('projects').document(pid)
        .collection('analyses')
        .order_by('submitted_at', direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )
    analyses = [d.to_dict() | {'id': d.id} for d in analyses_docs]

    # 載入資料集列表
    datasets_docs = (
        db.collection('projects').document(pid)
        .collection('datasets')
        .order_by('created_at', direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )
    datasets = [d.to_dict() | {'id': d.id} for d in datasets_docs]
    # 進專案頁主動同步 crawling 中的資料集（回收背景爬完的結果，狀態/下載鈕即時正確）
    datasets = [
        (_sync_crawling_dataset(pid, ds['id'], ds, allow_spawn=(role != 'viewer')) or ds)
        if ds.get('status') == 'crawling' else ds
        for ds in datasets
    ]

    # 載入「推薦筆記」（持久化的 ⓪ 內容發現結果）
    try:
        disc_docs = (db.collection('projects').document(pid)
                     .collection('discoveries')
                     .order_by('created_at', direction=firestore.Query.DESCENDING)
                     .limit(20).stream())
        discoveries = [d.to_dict() | {'id': d.id} for d in disc_docs]
    except Exception:
        discoveries = []
    # 草稿清單（供「加入現有草稿」下拉）
    draft_datasets = [{'id': ds['id'], 'name': ds.get('name', '')}
                      for ds in datasets if ds.get('status') == 'draft']

    # 品牌聲量探勘紀錄
    try:
        bs_docs = (db.collection('projects').document(pid)
                   .collection('brand_scans')
                   .order_by('created_at', direction=firestore.Query.DESCENDING)
                   .limit(10).stream())
        brand_scans = [d.to_dict() | {'id': d.id} for d in bs_docs]
    except Exception:
        brand_scans = []

    return render_template('project_detail.html',
                           project=project, pid=pid,
                           analyses=analyses, datasets=datasets, role=role,
                           discoveries=discoveries, draft_datasets=draft_datasets,
                           brand_scans=brand_scans,
                           is_admin=is_admin())


@bp.route('/<pid>/settings', methods=['POST'])
@project_access_required(min_role='owner')
def update_settings(pid, project, role):
    llm_provider = request.form.get('llm_provider', 'gemini').strip()
    llm_model = request.form.get('llm_model', 'gemini-2.5-flash').strip()
    llm_api_key = request.form.get('llm_api_key', '').strip()

    # 溫度（0–1，預設 0.3）與 thinking 開關（Gemini 2.5）
    try:
        temperature = float(request.form.get('temperature', 0.3))
        temperature = max(0.0, min(1.0, temperature))
    except (TypeError, ValueError):
        temperature = 0.3
    thinking = bool(request.form.get('thinking'))
    # 搜尋延伸（search-extent）開關：表單有 checkbox；勾選才開（預設值由 UI 決定）
    search_extent = bool(request.form.get('search_extent'))

    # 進階參數：輸出長度上限(A)、top_p、輸入內容量(B)
    try:
        max_output_tokens = int(request.form.get('max_output_tokens') or 8192)
        max_output_tokens = max(256, min(32768, max_output_tokens))
    except (TypeError, ValueError):
        max_output_tokens = 8192
    top_p_raw = request.form.get('top_p', '').strip()
    top_p = None
    if top_p_raw:
        try:
            top_p = max(0.0, min(1.0, float(top_p_raw)))
        except (TypeError, ValueError):
            top_p = None
    input_scale = request.form.get('input_scale', 'standard').strip().lower()
    if input_scale not in ('standard', 'large', 'max'):
        input_scale = 'standard'

    update = {
        'updated_at': firestore.SERVER_TIMESTAMP,
        'llm_config.provider': llm_provider,
        'llm_config.model': llm_model,
        'llm_config.temperature': temperature,
        'llm_config.thinking': thinking,
        'llm_config.search_extent': search_extent,
        'llm_config.max_output_tokens': max_output_tokens,
        'llm_config.top_p': top_p,
        'llm_config.input_scale': input_scale,
    }
    if llm_api_key:  # 只在有填寫時才更新 key（空白代表不變）
        update['llm_config.api_key'] = llm_api_key

    db.collection('projects').document(pid).update(update)
    flash('LLM 設定已更新。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))


# LLM 供應商模型查詢已抽出（見 llm_models.py）。
from ..llm_models import _fetch_provider_models  # noqa: F401

@bp.route('/<pid>/models')
@project_access_required(min_role='editor')
def list_models(pid, project, role):
    """回傳指定提供商的可用模型清單（用專案已儲存的 API key 即時抓取）。

    Query: ?provider=gemini|claude|openai
    回傳：{"models": [...]} 或 {"models": [], "error": "..."}
    """
    provider = request.args.get('provider', '').lower().strip()
    if provider not in ('gemini', 'claude', 'openai'):
        return jsonify({'models': [], 'error': '不支援的提供商'}), 400
    llm_cfg = project.get('llm_config', {}) or {}
    api_key = llm_cfg.get('api_key', '')
    # 安全：只允許查「與已儲存金鑰相符的提供商」。否則 Editor 可指定別家 provider，
    # 把 Owner 存的金鑰送往非該金鑰所屬的第三方 API（跨提供商金鑰外洩）。
    stored_provider = (llm_cfg.get('provider', '') or '').lower().strip()
    if stored_provider and provider != stored_provider:
        return jsonify({'models': [],
                        'error': f'此專案金鑰屬 {stored_provider}，無法用於查詢 {provider} 模型'}), 400
    if not api_key:
        return jsonify({'models': [], 'error': '尚未設定 API Key（請先儲存該提供商的 Key）'}), 200
    models = _fetch_provider_models(provider, api_key)
    if not models:
        return jsonify({'models': [],
                        'error': '無法取得模型清單（API Key 可能非此提供商，或暫時無法連線）'}), 200
    return jsonify({'models': models}), 200


@bp.route('/<pid>/members', methods=['POST'])
@project_access_required(min_role='owner')
def add_member(pid, project, role):
    member_email = request.form.get('email', '').strip().lower()
    member_role = request.form.get('role', 'viewer')

    if not member_email:
        flash('請填寫成員 email。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    _EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
    if not _EMAIL_RE.match(member_email):
        flash('Email 格式不正確。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if member_email == project.get('owner', '').lower():
        flash('該用戶已是 Owner，無法再新增為成員。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if member_role not in ('editor', 'viewer'):
        member_role = 'viewer'

    # ⚠️ 不可用 f'members.{email}' 欄位路徑：email 含「.」會被 Firestore 當巢狀路徑切開
    #   （members.user@gmail.com → members→"user@gmail"→"com"），導致成員 key 錯誤、共編者看不到專案。
    #   改 read-modify-write，email 作為 map 的字面 key。
    members = dict(project.get('members', {}) or {})
    members[member_email] = member_role
    db.collection('projects').document(pid).update({
        'members': members,
        'member_emails': list(members.keys()),   # N+1：與 members 同步（供 array_contains 查詢）
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    flash(f'已新增成員 {member_email}（{member_role}）。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))


@bp.route('/<pid>/members/remove', methods=['POST'])
@project_access_required(min_role='owner')
def remove_member(pid, project, role):
    member_email = request.form.get('email', '').strip().lower()
    if member_email:
        # 同 add_member：以 read-modify-write 移除字面 key（避免 email 的「.」被當欄位路徑）
        members = dict(project.get('members', {}) or {})
        members.pop(member_email, None)
        db.collection('projects').document(pid).update({
            'members': members,
            'member_emails': list(members.keys()),   # N+1：與 members 同步
            'updated_at': firestore.SERVER_TIMESTAMP,
        })
        flash(f'已移除成員 {member_email}。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))


# ──────────────────────────────────────────────────────────────────────
# 專案操作：編輯/更名、封存/還原、刪除、強制刪除
# ──────────────────────────────────────────────────────────────────────

@bp.route('/<pid>/edit', methods=['POST'])
@project_access_required(min_role='owner')
def edit_project(pid, project, role):
    """編輯專案名稱與描述（更名即改 title）。"""
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    if not title:
        flash('專案名稱不可空白。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    db.collection('projects').document(pid).update({
        'title': title[:200],
        'description': description[:2000],
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    log_usage('edit_project', detail=title, project_id=pid)
    flash('專案資料已更新。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))


@bp.route('/<pid>/archive', methods=['POST'])
@project_access_required(min_role='owner')
def archive_project(pid, project, role):
    """封存或還原專案（Owner/Admin）。封存不刪資料，僅限制 Editor/Viewer 進入。"""
    archive = request.form.get('archive') == '1'
    update = {'archived': archive, 'updated_at': firestore.SERVER_TIMESTAMP}
    if archive:
        update['archived_at'] = firestore.SERVER_TIMESTAMP
    db.collection('projects').document(pid).update(update)
    log_usage('archive_project' if archive else 'unarchive_project',
              detail=project.get('title', ''), project_id=pid)
    flash('專案已封存。' if archive else '專案已還原。', 'success')
    if archive:
        return redirect(url_for('project_bp.list_projects'))
    return redirect(url_for('project_bp.project_detail', pid=pid))


# 專案生命週期層已抽出（見 project_lifecycle.py）。
from ..project_lifecycle import _project_active_jobs, _cascade_delete_project  # noqa: F401

@bp.route('/<pid>/delete', methods=['POST'])
@project_access_required(min_role='owner')
def delete_project(pid, project, role):
    """刪除整個專案（Owner/Admin）。先檢查無執行中相依工作，才允許級聯刪除。"""
    active = _project_active_jobs(pid)
    if active:
        names = '、'.join(f'{t}「{n}」' for t, n, _ in active[:5])
        more = '…' if len(active) > 5 else ''
        flash(f'此專案有 {len(active)} 個執行中工作（{names}{more}），無法刪除。'
              '請先停止這些工作，或由管理員強制刪除。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    _cascade_delete_project(pid, cancel=False)
    log_usage('delete_project', detail=project.get('title', ''), project_id=pid)
    flash('專案及其所有資料集與分析已刪除。', 'success')
    return redirect(url_for('project_bp.list_projects'))


@bp.route('/<pid>/force-delete', methods=['POST'])
@project_access_required(min_role='owner')
def force_delete_project(pid, project, role):
    """強制刪除卡住的專案（僅系統 Admin）。先取消所有執行中工作，再整個刪除。"""
    if not is_admin():
        abort(403)
    _cascade_delete_project(pid, cancel=True)
    log_usage('force_delete_project', detail=project.get('title', ''), project_id=pid)
    flash('已強制取消所有執行中工作並刪除整個專案。', 'warning')
    return redirect(url_for('project_bp.list_projects'))


# ──────────────────────────────────────────────────────────────────────
# Analysis 路由
# ──────────────────────────────────────────────────────────────────────

@bp.route('/<pid>/analyses', methods=['POST'])
@project_access_required(min_role='editor')
def submit_analysis_route(pid, project, role):
    """提交分析任務。

    表單欄位：
      report_title  報告標題
      contents_json JSON 陣列（[{url,title,text,source_type}, ...]）
    """
    report_title = request.form.get('report_title', '').strip()
    contents_raw = request.form.get('contents_json', '').strip()

    if not report_title:
        flash('請填寫報告標題。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    try:
        contents = json.loads(contents_raw)
        if not isinstance(contents, list) or not contents:
            raise ValueError('contents 必須是非空陣列')
        if len(contents) > 100:
            raise ValueError('每次最多 100 篇內容')
        _VALID_SOURCE_TYPES = {'media', 'ecommerce', 'forum', 'dcard', 'youtube', 'direct', ''}
        truncated_count = 0
        for i, item in enumerate(contents):
            if not isinstance(item, dict):
                raise ValueError(f'第 {i+1} 筆內容格式錯誤')
            url = str(item.get('url', ''))[:2048]
            title = str(item.get('title', ''))[:512]
            raw_text = str(item.get('text') or item.get('content') or '')
            if len(raw_text) > 50000:
                truncated_count += 1
            text = raw_text[:50000]
            src = str(item.get('source_type', ''))
            if src not in _VALID_SOURCE_TYPES:
                src = ''
            contents[i] = {'url': url, 'title': title, 'text': text, 'source_type': src}
        if truncated_count > 0:
            flash(f'注意：有 {truncated_count} 篇文章超過 50,000 字元，已截斷後送出分析。', 'warning')
    except Exception as e:
        flash(f'內容格式錯誤：{e}。請貼入正確的 JSON 陣列。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    llm_config = project.get('llm_config', {})
    llm_api_key = llm_config.get('api_key', '')
    if not llm_api_key:
        flash('尚未設定 LLM API Key，請先至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    # 呼叫 analysis-pipeline
    result = submit_analysis(
        report_title=report_title,
        contents=contents,
        llm_provider=llm_config.get('provider', 'gemini'),
        llm_model=llm_config.get('model', 'gemini-2.5-flash'),
        llm_api_key=llm_api_key,
        temperature=llm_config.get('temperature', 0.3),
        thinking=llm_config.get('thinking', False),
        search_extent=llm_config.get('search_extent', True),
        max_output_tokens=llm_config.get('max_output_tokens', 8192),
        top_p=llm_config.get('top_p'),
        input_scale=llm_config.get('input_scale', 'standard'),
    )

    if 'error' in result:
        flash(f'提交失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    job_id = result.get('job_id')

    # 在 Firestore 建立分析記錄
    analysis_ref = (
        db.collection('projects').document(pid)
        .collection('analyses').document()
    )
    analysis_ref.set({
        'id': analysis_ref.id,
        'job_id': job_id,
        'report_title': report_title,
        'status': 'pending',
        'progress': 0,
        'log': '任務已提交，等待分析引擎處理...',
        'n_articles': len(contents),
        'llm_provider': llm_config.get('provider', 'gemini'),
        'llm_model': llm_config.get('model', 'gemini-2.5-flash'),
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None,
        'result_markdown': None,
    })

    log_usage('analyse', detail=report_title, count=len(contents), project_id=pid)
    flash(f'分析任務已提交（{len(contents)} 篇），正在處理中...', 'success')
    return redirect(url_for('project_bp.analysis_detail',
                            pid=pid, aid=analysis_ref.id))


# 分析狀態對帳層已抽出（見 analysis_store.py）。
from ..analysis_store import _analysis_ref, _reconcile_analysis, _reconcile_derive, _derived_label  # noqa: F401

@bp.route('/<pid>/analyses/<aid>')
@project_access_required(min_role='viewer')
def analysis_detail(pid, aid, project, role):
    """查看報告（若已完成）或顯示進度。"""
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict() | {'id': aid}
    # lazy 自癒（載入時對帳，不靠前端輪詢硬撐）：主分析 + 延伸報告各自補寫
    try:
        _reconcile_analysis(pid, aid, analysis)
    except Exception as e:
        print(f"[analysis] 對帳略過：{e}", flush=True)
    if analysis.get('derive_status') == 'running' and analysis.get('derive_job_id'):
        try:
            _reconcile_derive(pid, aid, analysis)
        except Exception as e:
            print(f"[derive] 對帳略過：{e}", flush=True)
    return render_template('analysis_detail.html',
                           project=project, pid=pid,
                           analysis=analysis, role=role)


@bp.route('/<pid>/analyses/<aid>/status')
@project_access_required(min_role='viewer')
def analysis_status(pid, aid, project, role):
    """輪詢分析進度（JSON）。前端每 3 秒呼叫一次。"""
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        return jsonify({'error': '找不到此分析任務'}), 404
    return jsonify(_reconcile_analysis(pid, aid, doc.to_dict()))


@bp.route('/<pid>/analyses/<aid>/download')
@project_access_required(min_role='viewer')
def download_analysis(pid, aid, project, role):
    """下載分析報告（.md 檔案）。"""
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        abort(404)

    analysis = doc.to_dict()
    if analysis.get('status') != 'completed':
        flash('報告尚未完成，無法下載。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    markdown = analysis.get('result_markdown', '')
    raw_title = analysis.get('report_title', 'report')
    filename = re.sub(r'[^\w\-. ]', '_', raw_title).strip()[:80] + '.md'

    stream = BytesIO(markdown.encode('utf-8'))
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype='text/markdown; charset=utf-8',
    )


# 三項數值分析的 CSV 下載（核實用）。kind 白名單對應 numeric_exports 的鍵。
_NUMERIC_EXPORT_KINDS = {
    'tfidf': 'TF-IDF關鍵字',
    'association': '關聯規則',
    'entities': '實體情感',
}


@bp.route('/<pid>/analyses/<aid>/download/<kind>.csv')
@project_access_required(min_role='viewer')
def download_analysis_csv(pid, aid, project, role, kind):
    """下載單項數值分析結果的 CSV（tfidf / association / entities）。"""
    if kind not in _NUMERIC_EXPORT_KINDS:
        abort(404)
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        abort(404)

    analysis = doc.to_dict()
    if analysis.get('status') != 'completed':
        flash('報告尚未完成，無法下載。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    exports = analysis.get('numeric_exports') or {}
    csv_text = exports.get(kind)
    if not csv_text:
        flash('此報告沒有數值匯出檔（可能是舊報告），請重新分析以產生。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    raw_title = analysis.get('report_title', 'report')
    base = re.sub(r'[^\w\-. ]', '_', raw_title).strip()[:60]
    filename = f"{base}_{kind}.csv"
    # utf-8-sig（含 BOM）讓 Excel 正確顯示中文
    stream = BytesIO(csv_text.encode('utf-8-sig'))
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype='text/csv; charset=utf-8',
    )


# ── 延伸行動報告：模型 A，啟用的知識庫專家 = 可產生的延伸報告類型 ──
# 分析師於主報告完成並認可後手動觸發。唯讀主報告、結果存母分析 analyses/{aid}.derived_reports
# （綁定該主報告；主報告換＝新 aid＝重產）。生成用「用戶專案的 LLM Key」，系統不負擔生成成本。

@bp.route('/<pid>/analyses/<aid>/derive', methods=['POST'])
@project_access_required(min_role='editor')
def derive_audience_reports(pid, aid, project, role):
    """觸發產生延伸行動報告（非同步），依後台啟用的知識庫專家。文字分析、completed 才可。"""
    doc = _analysis_ref(pid, aid).get()
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict()
    if analysis.get('status') != 'completed' or not analysis.get('result_markdown'):
        flash('主報告尚未完成，無法產生延伸報告。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))
    if analysis.get('kind') == 'visual':
        flash('視覺報告不支援延伸行動報告。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    experts = kb_store.list_enabled_experts()
    if not experts:
        flash('知識庫尚無啟用的專家，請先至 /admin/knowledge 建立並啟用。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    llm = project.get('llm_config', {}) or {}
    api_key = llm.get('api_key', '')
    if not api_key:
        flash('專案尚未設定 LLM API Key，無法產生延伸報告。請至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    payload_experts = [
        {'slug': e['slug'], 'label': e.get('label', e['slug']),
         'prompt': e.get('prompt', ''), 'playbook': e.get('playbook', '')}
        for e in experts
    ]
    res = submit_audience(
        report_title=analysis.get('report_title', '報告'),
        source_markdown=analysis.get('result_markdown', ''),
        experts=payload_experts,
        llm_provider=llm.get('provider', 'gemini'),
        llm_model=llm.get('model', 'gemini-2.5-flash'),
        llm_api_key=api_key,
    )
    job_id = res.get('job_id')
    if not job_id:
        flash(f"延伸報告提交失敗：{res.get('error', '未知錯誤')}", 'danger')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))

    # 存當下啟用的專家（slug+label），供報告頁顯示標題（與動態 slug 對應）
    _analysis_ref(pid, aid).update({
        'derive_job_id': job_id,
        'derive_status': 'running',
        'derive_experts': [{'slug': e['slug'], 'label': e.get('label', e['slug'])}
                           for e in experts],
        'derive_error': firestore.DELETE_FIELD,
    })
    flash('延伸報告產生中，稍候頁面會自動更新。', 'info')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))


@bp.route('/<pid>/analyses/<aid>/derive/status')
@project_access_required(min_role='viewer')
def derive_status(pid, aid, project, role):
    """輪詢延伸報告產生進度（JSON）。完成時把各份存回 analyses doc。"""
    doc = _analysis_ref(pid, aid).get()
    if not doc.exists:
        return jsonify({'status': 'error', 'error': '找不到分析'}), 404
    return jsonify(_reconcile_derive(pid, aid, doc.to_dict()))


@bp.route('/<pid>/analyses/<aid>/derived/<kind>')
@project_access_required(min_role='viewer')
def view_derived_report(pid, aid, project, role, kind):
    """檢視單份延伸報告（Markdown 渲染）。kind＝專家 slug（動態）。"""
    if not kb_store.slug_ok(kind):
        abort(404)
    doc = _analysis_ref(pid, aid).get()
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict()
    md = (analysis.get('derived_reports') or {}).get(kind)
    if not md:
        flash('此延伸報告尚未產生。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))
    return render_template('derived_report.html', pid=pid, aid=aid, project=project,
                           kind=kind, kind_label=_derived_label(analysis, kind),
                           report_title=analysis.get('report_title', ''),
                           markdown=md)


@bp.route('/<pid>/analyses/<aid>/derived/<kind>.md')
@project_access_required(min_role='viewer')
def download_derived_report(pid, aid, project, role, kind):
    """下載單份延伸報告（.md）。kind＝專家 slug（動態）。"""
    if not kb_store.slug_ok(kind):
        abort(404)
    doc = _analysis_ref(pid, aid).get()
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict()
    md = (analysis.get('derived_reports') or {}).get(kind)
    if not md:
        flash('此延伸報告尚未產生。', 'warning')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))
    raw_title = analysis.get('report_title', 'report')
    base = re.sub(r'[^\w\-. ]', '_', raw_title).strip()[:60]
    stream = BytesIO(md.encode('utf-8'))
    return send_file(stream, as_attachment=True,
                     download_name=f"{base}_{kind}.md",
                     mimetype='text/markdown; charset=utf-8')


@bp.route('/<pid>/analyses/<aid>/rename', methods=['POST'])
@project_access_required(min_role='editor')
def rename_analysis(pid, aid, project, role):
    """更名分析報告（report_title）。"""
    new_title = request.form.get('report_title', '').strip()[:200]
    if not new_title:
        flash('請填寫新的報告標題。', 'danger')
        return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))
    ref = (db.collection('projects').document(pid)
           .collection('analyses').document(aid))
    if not ref.get().exists:
        abort(404)
    ref.update({'report_title': new_title,
                'updated_at': firestore.SERVER_TIMESTAMP})
    flash('報告標題已更新。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=aid))


@bp.route('/<pid>/analyses/<aid>/delete', methods=['POST'])
@project_access_required(min_role='editor')
def delete_analysis(pid, aid, project, role):
    """刪除分析報告；若仍在進行中則先請求分析引擎強制停止（廢除執行階段），再移除記錄。"""
    ref = (db.collection('projects').document(pid)
           .collection('analyses').document(aid))
    doc = ref.get()
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict()
    status = analysis.get('status')
    job_id = analysis.get('job_id')

    stopped = False
    if status in ('pending', 'running') and job_id:
        res = cancel_analysis(job_id)
        stopped = 'error' not in res
        log_usage('stop_analysis', detail=analysis.get('report_title', ''),
                  project_id=pid)

    ref.delete()
    log_usage('delete_analysis', detail=analysis.get('report_title', ''),
              project_id=pid)
    if stopped:
        flash('已強制停止分析、廢除執行階段並刪除報告。', 'success')
    else:
        flash('分析報告已刪除。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))


# ──────────────────────────────────────────────────────────────────────
# 資料集（爬取文件）：輸入 URL → 後端非同步爬取 → 文件 → 一鍵分析
# Firestore: projects/{pid}/datasets/{did}
# ──────────────────────────────────────────────────────────────────────

@bp.route('/<pid>/analyses/combine', methods=['POST'])
@project_access_required(min_role='editor')
def combine_analyses(pid, project, role):
    """🧩 整合報告（階段③）：選 1 筆文字報告 + 1 筆視覺報告 → 整合策略報告。
    產物本身也存成 analyses（kind='combined'），列入歷史分析。"""
    ids = request.form.getlist('analysis_ids')
    if len(ids) < 2:
        flash('請勾選一份文字報告與一份視覺報告（共兩筆）。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    docs = []
    for aid in ids:
        d = (db.collection('projects').document(pid)
             .collection('analyses').document(aid).get())
        if d.exists:
            docs.append(d.to_dict())
    text_doc = next((v for v in docs if v.get('kind') not in ('visual', 'combined')), None)
    visual_doc = next((v for v in docs if v.get('kind') == 'visual'), None)
    if not text_doc or not visual_doc:
        flash('整合需要「一份文字報告」+「一份視覺報告」，請確認勾選。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if text_doc.get('status') != 'completed' or visual_doc.get('status') != 'completed':
        flash('兩份報告都需「已完成」才能整合。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    text_md = text_doc.get('result_markdown') or ''
    visual_md = visual_doc.get('result_markdown') or ''
    if not text_md or not visual_md:
        flash('報告內容尚未就緒，請稍候再試。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    cfg = project.get('llm_config', {}) or {}
    provider = (cfg.get('provider') or 'gemini').lower()
    model = cfg.get('model') or 'gemini-2.5-flash'
    api_key = cfg.get('api_key') or ''
    if not api_key:
        flash('請先在專案設定填入 LLM API Key。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    topic = (text_doc.get('report_title') or '').strip() or '整合主題'
    title = f"{topic}（整合報告）"
    result = submit_combined(title, text_md, visual_md, provider, model, api_key,
                             topic=topic, temperature=cfg.get('temperature', 0.3))
    if 'error' in result:
        flash(f'啟動整合失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    job_id = result.get('job_id')
    ref = (db.collection('projects').document(pid).collection('analyses').document())
    ref.set({
        'id': ref.id, 'job_id': job_id, 'kind': 'combined',
        'report_title': title, 'status': 'pending', 'progress': 0,
        'log': '整合報告已提交，等待處理...',
        'llm_provider': provider, 'llm_model': model,
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None, 'result_markdown': None,
        'source_text': text_doc.get('id'), 'source_visual': visual_doc.get('id'),
    })
    log_usage('combine', detail=title, count=2, project_id=pid)
    flash('已提交整合報告（文字 × 視覺）。完成後列入歷史分析。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=ref.id))


@bp.route('/<pid>/analyses/combined', methods=['POST'])
@project_access_required(min_role='editor')
def analyse_combined(pid, project, role):
    """合併多個資料集的成功項目，提交一份分析。

    例：爬蟲時尚媒體資料集 + Cowork 蒐集的 Dcard 資料集合併分析。
    form: dataset_ids（多選）+ report_title。
    """
    dataset_ids = request.form.getlist('dataset_ids')
    if not dataset_ids:
        flash('請至少勾選一個資料集。', 'warning')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    contents = []
    used_names = []
    for did in dataset_ids:
        doc = (db.collection('projects').document(pid)
               .collection('datasets').document(did).get())
        if not doc.exists:
            continue
        ds = doc.to_dict()
        if ds.get('status') != 'completed':
            continue
        used_names.append(ds.get('name', did))
        for it in _load_dataset_items(pid, did):
            if it.get('status') == 'success' and it.get('content'):
                contents.append({
                    'url': it.get('url', ''),
                    'title': it.get('title', ''),
                    'text': it.get('content', ''),
                    'source_type': 'media',
                })
    if not contents:
        flash('勾選的資料集中沒有可分析的成功項目（或尚未完成）。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if len(contents) > 100:
        flash(f'合併後共 {len(contents)} 篇，超過單次分析上限 100 篇，請減少資料集。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    llm_config = project.get('llm_config', {})
    if not llm_config.get('api_key'):
        flash('尚未設定 LLM API Key，請先至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    report_title = (request.form.get('report_title', '').strip()
                    or f'合併分析：{"、".join(used_names)[:50]}')

    result = submit_analysis(
        report_title=report_title,
        contents=contents,
        llm_provider=llm_config.get('provider', 'gemini'),
        llm_model=llm_config.get('model', 'gemini-2.5-flash'),
        llm_api_key=llm_config.get('api_key'),
        temperature=llm_config.get('temperature', 0.3),
        thinking=llm_config.get('thinking', False),
        search_extent=llm_config.get('search_extent', True),
        max_output_tokens=llm_config.get('max_output_tokens', 8192),
        top_p=llm_config.get('top_p'),
        input_scale=llm_config.get('input_scale', 'standard'),
    )
    if 'error' in result:
        flash(f'提交分析失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    job_id = result.get('job_id')
    analysis_ref = (db.collection('projects').document(pid)
                    .collection('analyses').document())
    analysis_ref.set({
        'id': analysis_ref.id,
        'job_id': job_id,
        'report_title': report_title,
        'status': 'pending',
        'progress': 0,
        'log': '任務已提交，等待分析引擎處理...',
        'n_articles': len(contents),
        'llm_provider': llm_config.get('provider', 'gemini'),
        'llm_model': llm_config.get('model', 'gemini-2.5-flash'),
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None,
        'result_markdown': None,
        'source_dataset': ','.join(dataset_ids),
    })
    log_usage('analyse', detail=report_title, count=len(contents), project_id=pid)
    flash(f'已合併 {len(used_names)} 個資料集提交分析（{len(contents)} 篇）。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=analysis_ref.id))


# ──────────────────────────────────────────────────────────────────────
# 資料集下載（原始爬取內文）：Markdown / JSON
# ──────────────────────────────────────────────────────────────────────

# 資料集匯出層已抽出（見 dataset_export.py）。
from ..dataset_export import _dataset_to_markdown, _dataset_to_json  # noqa: F401

# ── 領域子模組：套用各自的 @bp.route（須在 bp + 共用 helper 定義後）──
from . import datasets   # noqa: E402,F401
from . import discovery  # noqa: E402,F401
