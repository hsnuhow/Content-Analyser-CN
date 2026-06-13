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

from .services import db, get_admin_email, ensure_user
from .auth_guards import login_required
from .analysis_client import submit_analysis, get_job_status
from .crawler_client import submit_crawl_batch, get_crawl_status

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

    # 載入資料集列表
    datasets_docs = (
        db.collection('projects').document(pid)
        .collection('datasets')
        .order_by('created_at', direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )
    datasets = [d.to_dict() | {'id': d.id} for d in datasets_docs]

    return render_template('project_detail.html',
                           project=project, pid=pid,
                           analyses=analyses, datasets=datasets, role=role)


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


# ──────────────────────────────────────────────────────────────────────
# 資料集（爬取文件）：輸入 URL → 後端非同步爬取 → 文件 → 一鍵分析
# Firestore: projects/{pid}/datasets/{did}
# ──────────────────────────────────────────────────────────────────────

@bp.route('/<pid>/datasets', methods=['POST'])
@project_access_required(min_role='editor')
def create_dataset(pid, project, role):
    """提交 URL 清單，建立資料集並啟動 content-crawler 非同步爬取。"""
    name = request.form.get('name', '').strip()
    urls_raw = request.form.get('urls', '').strip()
    use_gemini = bool(request.form.get('use_gemini'))

    urls = [u.strip() for u in urls_raw.splitlines() if u.strip()]
    if not name:
        flash('請填寫資料集名稱。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if not urls:
        flash('請至少輸入一個網址。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))
    if len(urls) > 100:
        flash('每個資料集最多 100 個網址。', 'danger')
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
        'items': [],
        'created_by': current_user_email(),
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    flash(f'資料集「{name}」已建立，正在爬取 {len(urls)} 個網址...', 'success')
    return redirect(url_for('project_bp.dataset_detail', pid=pid, did=ds_ref.id))


@bp.route('/<pid>/datasets/<did>')
@project_access_required(min_role='viewer')
def dataset_detail(pid, did, project, role):
    doc = (db.collection('projects').document(pid)
           .collection('datasets').document(did).get())
    if not doc.exists:
        abort(404)
    dataset = doc.to_dict() | {'id': did}
    return render_template('dataset_detail.html',
                           project=project, pid=pid, dataset=dataset, role=role)


@bp.route('/<pid>/datasets/<did>/status')
@project_access_required(min_role='viewer')
def dataset_status(pid, did, project, role):
    """輪詢爬取進度；完成時把 crawler 結果同步進 dataset.items。"""
    ds_ref = (db.collection('projects').document(pid)
              .collection('datasets').document(did))
    doc = ds_ref.get()
    if not doc.exists:
        return jsonify({'error': '找不到此資料集'}), 404

    dataset = doc.to_dict()
    status = dataset.get('status', 'crawling')

    if status == 'crawling':
        job_id = dataset.get('crawl_job_id')
        if job_id:
            job = get_crawl_status(job_id)
            jstatus = job.get('status', status)
            update = {
                'progress': job.get('progress', dataset.get('progress', 0)),
                'log': job.get('log', dataset.get('log', '')),
                'updated_at': firestore.SERVER_TIMESTAMP,
            }
            if jstatus == 'completed':
                results = job.get('results', [])
                update['items'] = results
                update['status'] = 'completed'
                update['item_count'] = len(results)
                update['succeeded'] = sum(1 for r in results if r.get('status') == 'success')
            elif jstatus == 'failed':
                update['status'] = 'failed'
                update['log'] = job.get('log', '爬取失敗')
            ds_ref.update(update)
            return jsonify({'status': update.get('status', 'crawling'),
                            'progress': update['progress'], 'log': update['log']})

    return jsonify({'status': status,
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
    items = dataset.get('items', [])
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

    llm_config = project.get('llm_config', {})
    if not llm_config.get('api_key'):
        flash('尚未設定 LLM API Key，請先至專案設定填入。', 'danger')
        return redirect(url_for('project_bp.project_detail', pid=pid))

    report_title = request.form.get('report_title', '').strip() or dataset.get('name', '分析報告')

    result = submit_analysis(
        report_title=report_title,
        contents=contents,
        llm_provider=llm_config.get('provider', 'gemini'),
        llm_model=llm_config.get('model', 'gemini-2.0-flash'),
        llm_api_key=llm_config.get('api_key'),
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
        'llm_model': llm_config.get('model', 'gemini-2.0-flash'),
        'submitted_by': current_user_email(),
        'submitted_at': firestore.SERVER_TIMESTAMP,
        'completed_at': None,
        'result_markdown': None,
        'source_dataset': did,
    })
    flash(f'已從資料集「{dataset.get("name")}」提交分析（{len(contents)} 篇）。', 'success')
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
    return dataset, None


@bp.route('/<pid>/datasets/<did>/download.md')
@project_access_required(min_role='viewer')
def download_dataset_md(pid, did, project, role):
    """下載資料集原始爬取內文（Markdown）。"""
    dataset, resp = _get_completed_dataset_or_redirect(pid, did)
    if resp:
        return resp
    md = _dataset_to_markdown(dataset)
    fname = (dataset.get('name') or 'dataset').replace(' ', '_')
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
    fname = (dataset.get('name') or 'dataset').replace(' ', '_')
    return send_file(BytesIO(payload.encode('utf-8')), as_attachment=True,
                     download_name=f"{fname}.json",
                     mimetype='application/json; charset=utf-8')
