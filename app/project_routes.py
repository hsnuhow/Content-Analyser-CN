# -*- coding: utf-8 -*-
"""
Project 與 Analysis 路由

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

from .services import db, get_admin_email, ensure_user
from .auth_guards import login_required
from .analysis_client import (submit_analysis, get_job_status, cancel_analysis,
                              submit_image_analysis, get_image_analysis_status)
from .crawler_client import (submit_crawl_batch, get_crawl_status, cancel_crawl,
                             submit_research, get_research_status,
                             submit_extract_images, get_extract_images_status)

bp = Blueprint('project_bp', __name__, url_prefix='/projects')

# 自動續批最多輪數（每輪一個 ≤45 分批次補爬「未爬取」項），防失控。
AUTO_CONTINUE_MAX_ROUNDS = 15

# ──────────────────────────────────────────────────────────────────────
# 輔助函式
# ──────────────────────────────────────────────────────────────────────

def current_user_email() -> str:
    return session.get('user', {}).get('email', '')


def parse_url_list(raw: str) -> list:
    """容錯解析網址清單，回傳去重保序的 http(s) 網址。

    處理：真換行、被 URL 編碼的換行/空白（%0A/%0D/%20）、空白分隔、
    以及多個網址黏成一坨（用 lookahead 在每個 http(s):// 前切開）。
    """
    if not raw:
        return []
    raw = (raw.replace('%0D', '\n').replace('%0d', '\n')
              .replace('%0A', '\n').replace('%0a', '\n')
              .replace('%20', ' ').replace('%09', ' '))
    seen, out = set(), []
    for tok in re.split(r'\s+', raw.strip()):
        for part in re.split(r'(?=https?://)', tok):
            p = part.strip().strip('<>"\'，。、')
            if p.startswith(('http://', 'https://')) and p not in seen:
                seen.add(p)
                out.append(p)
    return out


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
            # 白名單 gate：非 approved（pending/rejected）不得訪問任何專案資源，
            # 即使其 email 被列為某專案 member。
            if session.get('whitelist_status') != 'approved':
                email = session['user'].get('email', '')
                if ensure_user(email) != 'approved':
                    return redirect(url_for('main_bp.pending'))
                session['whitelist_status'] = 'approved'
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

    # 我是 Owner 的 Projects
    owner_docs = db.collection('projects').where('owner', '==', email).stream()
    projects = [d.to_dict() | {'id': d.id} for d in owner_docs]

    # 我是成員的 Projects（Firestore 不直接支援 map key 查詢，用全掃方式）
    # 小規模可接受；大規模應建立 subcollection
    all_docs = db.collection('projects').stream()
    seen_ids = {p['id'] for p in projects}
    for d in all_docs:
        data = d.to_dict() | {'id': d.id}
        if d.id not in seen_ids and email in data.get('members', {}):
            projects.append(data)

    # 按建立時間排序；封存的排到最後（穩定排序，仍灰階顯示於同一列表）
    projects.sort(key=lambda p: p.get('created_at') or '', reverse=True)
    projects.sort(key=lambda p: 1 if p.get('archived') else 0)
    return render_template('projects.html', projects=projects, is_admin=is_admin())


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

    return render_template('project_detail.html',
                           project=project, pid=pid,
                           analyses=analyses, datasets=datasets, role=role,
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


def _fetch_provider_models(provider: str, api_key: str) -> list:
    """用 API key 即時抓取各家可用模型清單（REST，不依賴 SDK）。失敗回傳 []。"""
    provider = (provider or '').lower().strip()
    if not api_key:
        return []
    try:
        if provider == 'gemini':
            r = requests.get(
                'https://generativelanguage.googleapis.com/v1beta/models',
                params={'key': api_key}, timeout=10)
            if r.status_code != 200:
                return []
            out = []
            for m in r.json().get('models', []):
                methods = m.get('supportedGenerationMethods', [])
                if 'generateContent' in methods:
                    out.append(m.get('name', '').replace('models/', ''))
            return [x for x in out if x]
        elif provider == 'openai':
            r = requests.get('https://api.openai.com/v1/models',
                             headers={'Authorization': f'Bearer {api_key}'}, timeout=10)
            if r.status_code != 200:
                return []
            ids = [d.get('id', '') for d in r.json().get('data', [])]
            # 只留對話型模型（gpt / o 系列 / chatgpt）
            return sorted(i for i in ids if i and (i.startswith('gpt') or i.startswith('o') or i.startswith('chatgpt')))
        elif provider == 'claude':
            r = requests.get('https://api.anthropic.com/v1/models',
                             headers={'x-api-key': api_key,
                                      'anthropic-version': '2023-06-01'}, timeout=10)
            if r.status_code != 200:
                return []
            return [d.get('id', '') for d in r.json().get('data', []) if d.get('id')]
    except Exception as e:
        print(f"[models] {provider} 抓取失敗：{e}", flush=True)
    return []


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

    db.collection('projects').document(pid).update({
        f'members.{member_email}': member_role,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    flash(f'已新增成員 {member_email}（{member_role}）。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))


@bp.route('/<pid>/members/remove', methods=['POST'])
@project_access_required(min_role='owner')
def remove_member(pid, project, role):
    member_email = request.form.get('email', '').strip().lower()
    if member_email:
        db.collection('projects').document(pid).update({
            f'members.{member_email}': firestore.DELETE_FIELD,
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


@bp.route('/<pid>/analyses/<aid>')
@project_access_required(min_role='viewer')
def analysis_detail(pid, aid, project, role):
    """查看報告（若已完成）或顯示進度。"""
    doc = (db.collection('projects').document(pid)
           .collection('analyses').document(aid).get())
    if not doc.exists:
        abort(404)
    analysis = doc.to_dict() | {'id': aid}
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

    analysis = doc.to_dict()
    status = analysis.get('status', 'pending')

    # 若還在進行中，向 analysis-pipeline 查詢最新進度
    if status in ('pending', 'running'):
        job_id = analysis.get('job_id')
        if job_id:
            pipeline_status = get_job_status(job_id)
            new_status = pipeline_status.get('status', status)
            progress = pipeline_status.get('progress', analysis.get('progress', 0))
            log = pipeline_status.get('log', analysis.get('log', ''))

            update = {
                'status': new_status,
                'progress': progress,
                'log': log,
                'updated_at': firestore.SERVER_TIMESTAMP,
            }
            if new_status == 'completed':
                update['result_markdown'] = pipeline_status.get('result_markdown', '')
                update['completed_at'] = firestore.SERVER_TIMESTAMP
            if new_status == 'failed':
                update['log'] = pipeline_status.get('error', log)

            # 更新 Firestore
            (db.collection('projects').document(pid)
             .collection('analyses').document(aid).update(update))

            return jsonify({
                'status': new_status,
                'progress': progress,
                'log': log,
            })

    # 已完成或失敗
    return jsonify({
        'status': status,
        'progress': analysis.get('progress', 100 if status == 'completed' else 0),
        'log': analysis.get('log', ''),
    })


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

@bp.route('/<pid>/datasets', methods=['POST'])
@project_access_required(min_role='editor')
def create_dataset(pid, project, role):
    """提交 URL 清單，建立資料集並啟動 content-crawler 非同步爬取。"""
    name = request.form.get('name', '').strip()
    use_gemini = bool(request.form.get('use_gemini'))

    urls = parse_url_list(request.form.get('urls', ''))
    if not name:
        flash('請填寫資料集名稱。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if not urls:
        flash('請至少輸入一個網址。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if len(urls) > 1000:
        flash('單次最多 1000 個網址（如需更多請分次或用重爬續加）。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    # 爬蟲 selector 輔助用 Project 的 LLM Key（若為 gemini）
    llm_config = project.get('llm_config', {})
    gemini_key = llm_config.get('api_key') if llm_config.get('provider') == 'gemini' else None

    result = submit_crawl_batch(urls, use_gemini=use_gemini, gemini_api_key=gemini_key)
    if 'error' in result:
        flash(f'啟動爬取失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    crawl_job_id = result.get('job_id')
    ds_ref = db.collection('projects').document(pid).collection('datasets').document()
    ds_ref.set({
        'id': ds_ref.id,
        'name': name,
        'source_urls': urls,
        'crawl_job_id': crawl_job_id,
        'status': 'crawling',
        'progress': 0,
        'log': '已提交爬取任務...',
        'item_count': len(urls),
        'created_by': current_user_email(),
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    log_usage('crawl', detail=name, count=len(urls), project_id=pid)
    flash(f'資料集「{name}」已建立，正在爬取 {len(urls)} 個網址...', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=ds_ref.id))


@bp.route('/<pid>/datasets/manual', methods=['POST'])
@project_access_required(min_role='editor')
def create_manual_dataset(pid, project, role):
    """手動/上傳建立資料集（不經爬蟲）：供 Claude Cowork 等外部蒐集的內容匯入。

    輸入：name + items_json（貼上）或上傳檔 file（皆為 JSON 陣列）。
    每筆格式：{"url","title","text"}（text 亦相容 content）。
    直接建立 status=completed 的資料集，items 與爬蟲結果同 schema，可照常一鍵分析。
    """
    name = request.form.get('name', '').strip()
    raw = request.form.get('items_json', '').strip()
    # 上傳檔優先（限大小，避免大檔讀進記憶體 OOM）
    up = request.files.get('file')
    if up and up.filename:
        try:
            _MAX_UPLOAD = 3 * 1024 * 1024  # 3MB
            blob = up.read(_MAX_UPLOAD + 1)
            if len(blob) > _MAX_UPLOAD:
                flash('上傳檔過大（上限 3MB），請拆分後再匯入。', 'danger')
                return redirect(url_for('project_bp.project_detail', pid=pid))
            raw = blob.decode('utf-8', 'ignore').strip()
        except Exception:
            flash('上傳檔讀取失敗，請確認為 UTF-8 JSON。', 'danger')
            return redirect(url_for('project_bp.project_detail', pid=pid))

    if not name:
        flash('請填寫資料集名稱。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if not raw:
        flash('請貼上 JSON 或上傳檔案。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    try:
        data = json.loads(raw)
        if not isinstance(data, list) or not data:
            raise ValueError('內容必須是非空 JSON 陣列')
        if len(data) > 1000:
            raise ValueError('每個資料集最多 1000 筆')
    except Exception as e:
        flash(f'JSON 解析失敗：{e}', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    items = []
    for i, it in enumerate(data):
        if not isinstance(it, dict):
            flash(f'第 {i+1} 筆不是物件。', 'danger')
            return redirect(url_for('project_bp.project_detail', pid=pid))
        text = str(it.get('text') or it.get('content') or '').strip()
        title = str(it.get('title') or '').strip() or f'項目 {i+1}'
        url = str(it.get('url') or '').strip()
        if not text:
            continue  # 跳過無內文的項目
        text = text[:50000]
        items.append({
            'url': url,
            'title': title,
            'content': text,
            'length': len(text),
            'status': 'success',
            'source': 'manual',
        })
    if not items:
        flash('沒有可用項目（每筆需有 text/content）。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    # items 改存子集合（無 1MB 上限），dataset 文件只放 metadata。
    succeeded = len(items)
    ds_ref = db.collection('projects').document(pid).collection('datasets').document()
    ds_ref.set({
        'id': ds_ref.id,
        'name': name,
        'source_urls': [it['url'] for it in items if it['url']],
        'crawl_job_id': None,
        'status': 'completed',
        'progress': 100,
        'log': f'手動匯入 {succeeded} 筆',
        'item_count': succeeded,
        'succeeded': succeeded,
        'failed': 0,
        'origin': 'manual',
        'created_by': current_user_email(),
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    _save_dataset_items(pid, ds_ref.id, items)
    log_usage('manual_import', detail=name, count=succeeded, project_id=pid)
    flash(f'資料集「{name}」已匯入 {succeeded} 筆，可直接分析。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=ds_ref.id))


# ──────────────────────────────────────────────────────────────────────
# 資料集 items 子集合（projects/{pid}/datasets/{did}/items）
# 內文存子集合而非內嵌於 dataset 文件 → 無單文件 1MB 上限、筆數不受限。
# 每筆用 auto-id 文件 + `_seq` 單調遞增欄位（刪除後 append 不撞 id），讀取依 `_seq` 排序。
# ──────────────────────────────────────────────────────────────────────

def _items_ref(pid: str, did: str):
    return (db.collection('projects').document(pid)
            .collection('datasets').document(did).collection('items'))


def _load_dataset_items(pid: str, did: str) -> list:
    try:
        items = [d.to_dict() for d in _items_ref(pid, did).order_by('_seq').stream()]
        if items:
            return items
    except Exception as e:
        print(f"[items] 子集合讀取失敗 {did}: {e}", flush=True)
    # 後備（向後相容）：舊格式 items 內嵌於 dataset 文件，子集合空時讀回。
    try:
        doc = (db.collection('projects').document(pid)
               .collection('datasets').document(did).get())
        return (doc.to_dict() or {}).get('items', []) if doc.exists else []
    except Exception:
        return []


def _save_dataset_items(pid: str, did: str, items: list, append: bool = False) -> int:
    """寫入 items。append=False 先清空既有。回傳寫入後的 _next_seq。"""
    ref = _items_ref(pid, did)
    ds_ref = db.collection('projects').document(pid).collection('datasets').document(did)
    items = list(items)
    count = len(items)
    if not append:
        for d in ref.stream():
            d.reference.delete()
        seq = 0
    else:
        # 並發安全：用交易「預約」一段連續的 _seq（count 個）。避免兩個併發 append
        # （雙開分頁/續批）讀到同一 _next_seq → items _seq 重疊、計數器被後者覆蓋。
        @firestore.transactional
        def _reserve(t):
            snap = ds_ref.get(transaction=t)
            start = (snap.to_dict() or {}).get('_next_seq', 0) if snap.exists else 0
            t.set(ds_ref, {'_next_seq': start + count}, merge=True)
            return start
        seq = _reserve(db.transaction())
    batch = db.batch()
    n = 0
    for it in items:
        batch.set(ref.document(), {**it, '_seq': seq})
        seq += 1
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = db.batch()
    if n % 400 != 0:
        batch.commit()
    if not append:
        ds_ref.update({'_next_seq': seq})  # 覆寫模式無競爭，直接設定
    return seq


def _delete_dataset_items(pid: str, did: str) -> None:
    try:
        for d in _items_ref(pid, did).stream():
            d.reference.delete()
    except Exception:
        pass


def _replace_items_by_url(pid: str, did: str, urls_set: set, new_items: list) -> None:
    """recrawl-failed：刪除 url 在 urls_set 的舊 item，再 append new_items（保留已成功項）。"""
    ref = _items_ref(pid, did)
    for d in ref.stream():
        if (d.to_dict() or {}).get('url') in urls_set:
            d.reference.delete()
    _save_dataset_items(pid, did, new_items, append=True)


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
    job_id = dataset.get('crawl_job_id')
    if not job_id:
        return dataset

    job = get_crawl_status(job_id)
    if not isinstance(job, dict):
        return dataset
    jstatus = job.get('status', 'crawling')
    update = {
        'progress': job.get('progress', dataset.get('progress', 0)),
        'log': job.get('log', dataset.get('log', '')),
        'updated_at': firestore.SERVER_TIMESTAMP,
    }
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
        if recrawl_urls:
            update['recrawl_urls'] = firestore.DELETE_FIELD
        if dataset.get('auto_round'):
            update['auto_round'] = firestore.DELETE_FIELD
    elif jstatus == 'failed':
        update['status'] = 'failed'
        update['log'] = job.get('log', '爬取失敗')
    ds_ref.update(update)
    return {**dataset, **update}


@bp.route('/<pid>/datasets/<did>')
@project_access_required(min_role='viewer')
def dataset_detail(pid, did, project, role):
    # 進入詳情頁主動同步：離開頁面後 crawler 跑完的結果在此回收。
    dataset = _sync_crawling_dataset(pid, did, allow_spawn=(role != 'viewer'))
    if dataset is None:
        abort(404)
    dataset['id'] = did
    dataset['items'] = _load_dataset_items(pid, did)  # items 改存子集合
    return render_template('dataset_detail.html',
                           project=project, pid=pid, dataset=dataset, role=role)


@bp.route('/<pid>/datasets/<did>/status')
@project_access_required(min_role='viewer')
def dataset_status(pid, did, project, role):
    """前端輪詢進度；同步邏輯共用 _sync_crawling_dataset。"""
    dataset = _sync_crawling_dataset(pid, did, allow_spawn=(role != 'viewer'))
    if dataset is None:
        return jsonify({'error': '找不到此資料集'}), 404
    return jsonify({'status': dataset.get('status', 'crawling'),
                    'progress': dataset.get('progress', 0),
                    'log': dataset.get('log', '')})


@bp.route('/<pid>/datasets/<did>/analyse', methods=['POST'])
@project_access_required(min_role='editor')
def analyse_dataset(pid, did, project, role):
    """一鍵：把資料集的成功項目送往 analysis-pipeline。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()

    if dataset.get('status') != 'completed':
        flash('資料集尚未爬取完成，無法分析。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    # 取成功項目，轉成 analysis 的 contents（爬蟲回傳 content 欄位）
    items = _load_dataset_items(pid, did)
    contents = [
        {
            'url': it.get('url', ''),
            'title': it.get('title', ''),
            'text': it.get('content', ''),       # analysis 相容 content，但統一帶 text
            'source_type': 'media',
        }
        for it in items if it.get('status') == 'success' and it.get('content')
    ]
    if not contents:
        flash('資料集中沒有可分析的成功項目。', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    # 與 submit_analysis_route 一致的保護：單次最多 100 篇、每篇截斷 50,000 字元
    # （資料集可達 1000 筆，未設限會把超量 payload 丟給 analysis-pipeline → 下游失敗/成本爆）。
    if len(contents) > 100:
        flash(f'成功項目 {len(contents)} 篇超過單次分析上限 100 篇，僅取前 100 篇。', 'warning')
        contents = contents[:100]
    truncated_count = 0
    for it in contents:
        if len(it['text']) > 50000:
            it['text'] = it['text'][:50000]
            truncated_count += 1
    if truncated_count:
        flash(f'注意：{truncated_count} 篇超過 50,000 字元，已截斷後送出。', 'warning')

    llm_config = project.get('llm_config', {})
    if not llm_config.get('api_key'):
        flash('尚未設定 LLM API Key，請先至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    report_title = request.form.get('report_title', '').strip() or dataset.get('name', '分析報告')

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
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

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
        'source_dataset': did,
    })
    log_usage('analyse', detail=report_title, count=len(contents), project_id=pid)
    flash(f'已從資料集「{dataset.get("name")}」提交分析（{len(contents)} 篇）。', 'success')
    return redirect(url_for('project_bp.analysis_detail', pid=pid, aid=analysis_ref.id))


@bp.route('/<pid>/datasets/<did>/recrawl', methods=['POST'])
@project_access_required(min_role='editor')
def recrawl_dataset(pid, did, project, role):
    """重啟/續爬一個資料集：
      mode=failed（預設）：只重爬未成功（失敗/未爬）的項，保留已成功項並合併。
      mode=all：整份重爬。
    解決批次被時限切掉、或部分站台失敗後不必整批重來。
    """
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()
    if dataset.get('status') == 'crawling':
        flash('資料集正在爬取中，請先等待完成或強制停止。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    mode = request.form.get('mode', 'failed')
    items = _load_dataset_items(pid, did)
    all_urls = dataset.get('source_urls') or [it.get('url') for it in items if it.get('url')]
    success_urls = {it.get('url') for it in items
                    if it.get('status') == 'success' and it.get('content')}
    if mode == 'all':
        target_urls = [u for u in all_urls if u]
    else:
        target_urls = [u for u in all_urls if u and u not in success_urls]
    target_urls = list(dict.fromkeys(target_urls))  # 去重保序
    if not target_urls:
        flash('沒有需要重爬的項目（全部已成功）。', 'info')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    llm_config = project.get('llm_config', {})
    gemini_key = llm_config.get('api_key') if llm_config.get('provider') == 'gemini' else None
    result = submit_crawl_batch(target_urls, use_gemini=bool(gemini_key),
                                gemini_api_key=gemini_key)
    if 'error' in result:
        flash(f'啟動重爬失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    update = {
        'crawl_job_id': result.get('job_id'),
        'status': 'crawling',
        'progress': 0,
        'log': f'重新爬取 {len(target_urls)} 項（{mode}）...',
        'updated_at': firestore.SERVER_TIMESTAMP,
    }
    if mode == 'all':
        update['recrawl_urls'] = firestore.DELETE_FIELD  # 完成時整批替換
    else:
        update['recrawl_urls'] = target_urls  # 完成時只替換這些 url，保留已成功項
    (db.collection('projects').document(pid)
     .collection('datasets').document(did).update(update))
    log_usage('recrawl', detail=f"{dataset.get('name', '')}({mode})",
              count=len(target_urls), project_id=pid)
    flash(f'已啟動重爬 {len(target_urls)} 項，完成後{"整批替換" if mode == "all" else "合併保留已成功項"}。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/research', methods=['POST'])
@project_access_required(min_role='editor')
def research_dataset(pid, did, project, role):
    """🔬 研究失敗項：對資料集中失敗的 URL 觸發選擇器研究 agent（on-demand）。
    產出候選選擇器（待 admin 確認升級）與失敗診斷。與爬蟲不並行。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()
    if dataset.get('status') == 'crawling':
        flash('資料集正在爬取中，請先等爬完再研究。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    items = _load_dataset_items(pid, did)
    failed_urls = list(dict.fromkeys(
        it.get('url') for it in items
        if it.get('status') == 'failed' and it.get('url')))
    if not failed_urls:
        flash('沒有失敗的項目可研究。', 'info')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    result = submit_research(failed_urls)
    if 'error' in result:
        flash(f'啟動研究失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    (db.collection('projects').document(pid).collection('datasets').document(did)
     .update({'research_job_id': result.get('job_id'),
              'research_status': 'running',
              'updated_at': firestore.SERVER_TIMESTAMP}))
    log_usage('research', detail=dataset.get('name', ''),
              count=len(failed_urls), project_id=pid)
    flash(f'已啟動「失敗項研究」（{len(failed_urls)} 個 URL）。完成後此頁會顯示候選選擇器與診斷。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/research/status')
@project_access_required(min_role='viewer')
def research_status(pid, did, project, role):
    """輪詢研究任務進度與結果（JSON）。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        return jsonify({'error': '找不到資料集'}), 404
    job_id = (ds_doc.to_dict() or {}).get('research_job_id')
    if not job_id:
        return jsonify({'status': 'none'}), 200
    job = get_research_status(job_id)
    return jsonify({
        'status': job.get('status', 'unknown'),
        'log': job.get('log', ''),
        'result': job.get('result', {}),
    }), 200


@bp.route('/<pid>/datasets/<did>/extract-images', methods=['POST'])
@project_access_required(min_role='editor')
def extract_images_dataset(pid, did, project, role):
    """🖼 擷取主文大圖：對資料集成功項的 URL 觸發影像擷取（只取主文大圖、不碰文字）。
    與文字爬取嚴格分離（獨立 crawler 端點），on-demand。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()
    if dataset.get('status') == 'crawling':
        flash('資料集正在爬取中，請先等爬完再擷取大圖。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    items = _load_dataset_items(pid, did)
    urls = list(dict.fromkeys(
        it.get('url') for it in items
        if it.get('status') == 'success' and it.get('url')))
    if not urls:
        flash('沒有成功的項目可擷取大圖。', 'info')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    result = submit_extract_images(urls)
    if 'error' in result:
        flash(f'啟動影像擷取失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    (db.collection('projects').document(pid).collection('datasets').document(did)
     .update({'image_job_id': result.get('job_id'),
              'image_status': 'running',
              'updated_at': firestore.SERVER_TIMESTAMP}))
    log_usage('extract_images', detail=dataset.get('name', ''),
              count=len(urls), project_id=pid)
    flash(f'已啟動「主文大圖擷取」（{len(urls)} 個成功項）。完成後此頁會顯示每篇抽到的大圖。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/extract-images/status')
@project_access_required(min_role='viewer')
def extract_images_status(pid, did, project, role):
    """輪詢影像擷取任務進度與結果（JSON）。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        return jsonify({'error': '找不到資料集'}), 404
    job_id = (ds_doc.to_dict() or {}).get('image_job_id')
    if not job_id:
        return jsonify({'status': 'none'}), 200
    job = get_extract_images_status(job_id)
    return jsonify({
        'status': job.get('status', 'unknown'),
        'log': job.get('log', ''),
        'n_images': job.get('n_images', 0),
        'results': job.get('results', []),
    }), 200


@bp.route('/<pid>/datasets/<did>/analyse-images', methods=['POST'])
@project_access_required(min_role='editor')
def analyse_images_dataset(pid, did, project, role):
    """🎨 大圖視覺分析（階段②）：把已擷取的主文大圖送 analysis-pipeline 做
    色調/色澤/主題/視覺吸睛要素分析，產出視覺報告。需先完成「擷取主文大圖」。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        abort(404)
    dataset = ds_doc.to_dict()
    image_job_id = dataset.get('image_job_id')
    if not image_job_id:
        flash('請先「擷取主文大圖」，再進行視覺分析。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    job = get_extract_images_status(image_job_id)
    if job.get('status') != 'completed':
        flash('大圖擷取尚未完成，請稍候再分析。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    images = []
    for it in (job.get('results') or []):
        src_url = it.get('url')
        for im in (it.get('images') or []):
            if im.get('src'):
                images.append({'src': im['src'], 'alt': im.get('alt', ''),
                               'source_url': src_url})
    if not images:
        flash('沒有可分析的大圖。', 'info')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    cfg = project.get('llm_config', {}) or {}
    provider = (cfg.get('provider') or 'gemini').lower()
    model = cfg.get('model') or 'gemini-2.5-flash'
    api_key = cfg.get('api_key') or ''
    if not api_key:
        flash('請先在專案設定填入 LLM API Key。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    if provider not in ('gemini', 'claude'):
        flash('影像視覺分析僅支援 Gemini 或 Claude（建議 Gemini），請於專案設定切換。', 'warning')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    title = f"{dataset.get('name', '')}（視覺分析）"
    result = submit_image_analysis(title, images, provider, model, api_key,
                                   temperature=cfg.get('temperature', 0.3))
    if 'error' in result:
        flash(f'啟動視覺分析失敗：{result["error"]}', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))

    (db.collection('projects').document(pid).collection('datasets').document(did)
     .update({'image_analysis_job_id': result.get('job_id'),
              'image_analysis_status': 'running',
              'updated_at': firestore.SERVER_TIMESTAMP}))
    log_usage('analyse_images', detail=dataset.get('name', ''),
              count=len(images), project_id=pid)
    flash(f'已啟動「大圖視覺分析」（{len(images)} 張）。完成後此頁會顯示視覺報告。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/analyse-images/status')
@project_access_required(min_role='viewer')
def analyse_images_status(pid, did, project, role):
    """輪詢大圖視覺分析任務進度與結果（result_markdown）。"""
    ds_doc = (db.collection('projects').document(pid)
              .collection('datasets').document(did).get())
    if not ds_doc.exists:
        return jsonify({'error': '找不到資料集'}), 404
    job_id = (ds_doc.to_dict() or {}).get('image_analysis_job_id')
    if not job_id:
        return jsonify({'status': 'none'}), 200
    job = get_image_analysis_status(job_id)
    return jsonify({
        'status': job.get('status', 'unknown'),
        'log': job.get('log', ''),
        'n_images': job.get('n_images'),
        'n_success': job.get('n_success'),
        'result_markdown': job.get('result_markdown', ''),
    }), 200


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

def _dataset_to_markdown(dataset: dict) -> str:
    """資料集 → Markdown：成功項目逐篇（標題/網址/字數/內文），末尾附未成功清單。"""
    name = dataset.get('name', 'dataset')
    items = dataset.get('items', [])
    success = [it for it in items if it.get('status') == 'success' and it.get('content')]
    others = [it for it in items if not (it.get('status') == 'success' and it.get('content'))]

    lines = [f"# {name}", "",
             f"> 共 {dataset.get('item_count', len(items))} 個網址，成功 {len(success)} 篇", ""]
    for it in success:
        lines += [f"## {it.get('title') or '(無標題)'}", "",
                  f"- 網址：{it.get('url', '')}",
                  f"- 字數：{it.get('length', '-')}", "",
                  it.get('content', ''), "", "---", ""]
    if others:
        lines += ["## 未成功項目", ""]
        for it in others:
            err = f" — {it.get('error')}" if it.get('error') else ""
            lines.append(f"- [{it.get('status', '?')}] {it.get('url', '')}{err}")
        lines.append("")
    return "\n".join(lines)


def _dataset_to_json(dataset: dict) -> dict:
    """資料集 → 結構化 JSON：含全部項目（成功+失敗）。"""
    items = dataset.get('items', [])
    return {
        'dataset': dataset.get('name', ''),
        'item_count': dataset.get('item_count', len(items)),
        'succeeded': sum(1 for it in items if it.get('status') == 'success'),
        'items': [
            {
                'url': it.get('url', ''),
                'title': it.get('title', ''),
                'length': it.get('length'),
                'status': it.get('status', ''),
                'content': it.get('content', ''),
                'error': it.get('error', ''),
            } for it in items
        ],
    }


def _get_completed_dataset_or_redirect(pid: str, did: str):
    """讀取已完成的資料集；未完成回 (None, redirect_response)。"""
    doc = (db.collection('projects').document(pid)
           .collection('datasets').document(did).get())
    if not doc.exists:
        abort(404)
    dataset = doc.to_dict()
    if dataset.get('status') != 'completed':
        flash('資料集尚未爬取完成，無法下載。', 'warning')
        return None, redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    dataset['items'] = _load_dataset_items(pid, did)  # items 改存子集合
    return dataset, None


@bp.route('/<pid>/datasets/<did>/download.md')
@project_access_required(min_role='viewer')
def download_dataset_md(pid, did, project, role):
    """下載資料集原始爬取內文（Markdown）。"""
    dataset, resp = _get_completed_dataset_or_redirect(pid, did)
    if resp:
        return resp
    md = _dataset_to_markdown(dataset)
    fname = re.sub(r'[^\w\-. ]', '_', (dataset.get('name') or 'dataset')).strip()[:80]
    return send_file(BytesIO(md.encode('utf-8')), as_attachment=True,
                     download_name=f"{fname}.md",
                     mimetype='text/markdown; charset=utf-8')


@bp.route('/<pid>/datasets/<did>/download.json')
@project_access_required(min_role='viewer')
def download_dataset_json(pid, did, project, role):
    """下載資料集原始爬取內文（結構化 JSON，含全部項目）。"""
    dataset, resp = _get_completed_dataset_or_redirect(pid, did)
    if resp:
        return resp
    payload = json.dumps(_dataset_to_json(dataset), ensure_ascii=False, indent=2)
    fname = re.sub(r'[^\w\-. ]', '_', (dataset.get('name') or 'dataset')).strip()[:80]
    return send_file(BytesIO(payload.encode('utf-8')), as_attachment=True,
                     download_name=f"{fname}.json",
                     mimetype='application/json; charset=utf-8')


# ──────────────────────────────────────────────────────────────────────
# 資料集更名 / 刪除（含強制停止爬取）
# ──────────────────────────────────────────────────────────────────────

@bp.route('/<pid>/datasets/<did>/rename', methods=['POST'])
@project_access_required(min_role='editor')
def rename_dataset(pid, did, project, role):
    """更名資料集。"""
    new_name = request.form.get('name', '').strip()[:200]
    if not new_name:
        flash('請填寫新的資料集名稱。', 'danger')
        return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))
    ref = (db.collection('projects').document(pid)
           .collection('datasets').document(did))
    if not ref.get().exists:
        abort(404)
    ref.update({'name': new_name, 'updated_at': firestore.SERVER_TIMESTAMP})
    flash('資料集名稱已更新。', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=did))


@bp.route('/<pid>/datasets/<did>/delete', methods=['POST'])
@project_access_required(min_role='editor')
def delete_dataset(pid, did, project, role):
    """刪除資料集；若仍在爬取中則先請求爬蟲強制停止（廢除執行階段），再移除記錄。"""
    ref = (db.collection('projects').document(pid)
           .collection('datasets').document(did))
    doc = ref.get()
    if not doc.exists:
        abort(404)
    dataset = doc.to_dict()
    status = dataset.get('status')
    crawl_job_id = dataset.get('crawl_job_id')

    stopped = False
    if status == 'crawling' and crawl_job_id:
        res = cancel_crawl(crawl_job_id)
        stopped = 'error' not in res
        log_usage('stop_crawl', detail=dataset.get('name', ''), project_id=pid)

    _delete_dataset_items(pid, did)  # 先刪 items 子集合
    ref.delete()
    log_usage('delete_dataset', detail=dataset.get('name', ''), project_id=pid)
    if stopped:
        flash('已強制停止爬取、廢除執行階段並刪除資料集。', 'success')
    else:
        flash('資料集已刪除。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))
