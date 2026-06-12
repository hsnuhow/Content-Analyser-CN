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
from functools import wraps
from flask import (Blueprint, render_template, request, jsonify,
                   session, redirect, url_for, flash, send_file, abort)
from firebase_admin import firestore
from io import BytesIO

from .services import db, get_admin_email
from .analysis_client import submit_analysis, get_job_status

bp = Blueprint('project_bp', __name__, url_prefix='/projects')

# ──────────────────────────────────────────────────────────────────────
# 輔助函式
# ──────────────────────────────────────────────────────────────────────

def current_user_email() -> str:
    return session.get('user', {}).get('email', '')


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
    return members.get(email) or members.get(email.lower())


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('main_bp.auth'))
        if session.get('whitelist_status') == 'pending':
            return redirect(url_for('main_bp.pending'))
        return f(*args, **kwargs)
    return decorated


def project_access_required(min_role: str = 'viewer'):
    """確認用戶有 Project 存取權。min_role: 'viewer' | 'editor' | 'owner'。"""
    ROLE_LEVEL = {'viewer': 1, 'editor': 2, 'owner': 3}

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('main_bp.auth'))
            pid = kwargs.get('pid')
            project = get_project(pid)
            if not project:
                abort(404)
            role = get_user_role(project, current_user_email())
            if not role or ROLE_LEVEL.get(role, 0) < ROLE_LEVEL.get(min_role, 1):
                abort(403)
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

    # 按建立時間排序
    projects.sort(key=lambda p: p.get('created_at') or '', reverse=True)
    return render_template('projects.html', projects=projects)


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
            'model': 'gemini-2.0-flash',
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
    return render_template('project_detail.html',
                           project=project, pid=pid,
                           analyses=analyses, role=role)


@bp.route('/<pid>/settings', methods=['POST'])
@project_access_required(min_role='owner')
def update_settings(pid, project, role):
    llm_provider = request.form.get('llm_provider', 'gemini').strip()
    llm_model = request.form.get('llm_model', 'gemini-2.0-flash').strip()
    llm_api_key = request.form.get('llm_api_key', '').strip()

    update = {
        'updated_at': firestore.SERVER_TIMESTAMP,
        'llm_config.provider': llm_provider,
        'llm_config.model': llm_model,
    }
    if llm_api_key:  # 只在有填寫時才更新 key（空白代表不變）
        update['llm_config.api_key'] = llm_api_key

    db.collection('projects').document(pid).update(update)
    flash('LLM 設定已更新。', 'success')
    return redirect(url_for('project_bp.project_detail', pid=pid))


@bp.route('/<pid>/members', methods=['POST'])
@project_access_required(min_role='owner')
def add_member(pid, project, role):
    member_email = request.form.get('email', '').strip().lower()
    member_role = request.form.get('role', 'viewer')

    if not member_email:
        flash('請填寫成員 email。', 'danger')
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
        llm_model=llm_config.get('model', 'gemini-2.0-flash'),
        llm_api_key=llm_api_key,
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
        'llm_model': llm_config.get('model', 'gemini-2.0-flash'),
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None,
        'result_markdown': None,
    })

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
    report_title = analysis.get('report_title', 'report').replace(' ', '_')
    filename = f"{report_title}.md"

    stream = BytesIO(markdown.encode('utf-8'))
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype='text/markdown; charset=utf-8',
    )
