# -*- coding: utf-8 -*-
"""
主路由（Phase 0 清理版）

Phase 0 變更：
  - 移除 hardcode ADMIN_EMAIL，改從 Firestore system/config 讀取
  - 移除 analysis_pipeline / export_utils 的 import
  - 移除 DOCX 下載路由（/download_project）
  - /submit_task、/task_status、/stop_task 改為 503 stub，
    待 Phase 2（analysis-pipeline）與 Phase 3（控制平面）重建

Phase 3 將重建：
  - /submit_task → 提交內容給 analysis-pipeline
  - /task_status → 查詢 Firestore analyses/{id} 狀態
  - /stop_task   → 取消分析任務
  - /download_project → 下載 Markdown 報告
"""
import os
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from firebase_admin import firestore
from .services import db, get_admin_email, ensure_user, update_last_login, get_user
from .auth_guards import login_required, is_dev_env
from . import oauth

bp = Blueprint('main_bp', __name__)


@bp.route('/debug')
def debug():
    # 安全：僅本地開發環境可用；正式環境回 404，避免洩漏 session/設定狀態。
    if not is_dev_env():
        return "Not Found", 404
    return jsonify({
        'session_user': session.get('user'),
        'is_dev_env': is_dev_env(),
        'flask_debug': os.environ.get('FLASK_DEBUG'),
        'admin_email_configured': get_admin_email() is not None,
    })


@bp.route('/')
@login_required
def index():
    """首頁：重定向到 Projects 列表。"""
    return redirect(url_for('project_bp.list_projects'))


@bp.route('/pending')
def pending():
    """等待管理員授權的頁面。"""
    user = session.get('user')
    if not user:
        return redirect(url_for('main_bp.auth'))
    if session.get('whitelist_status') == 'approved':
        return redirect(url_for('project_bp.list_projects'))
    return render_template('pending.html', user=user)


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_email = session['user']['email']
    user_ref = db.collection('users').document(user_email)

    if request.method == 'POST':
        # Phase 0：保留 API Key 儲存邏輯，Phase 3 將擴充為完整 LLM 設定
        api_key = request.form.get('gemini_api_key')
        try:
            user_ref.set({
                'gemini_api_key': api_key,
                'updated_at': firestore.SERVER_TIMESTAMP
            }, merge=True)
            flash('API Key 已更新。', 'success')
        except Exception as e:
            print(f"[Profile] Failed to update key: {e}")
            flash('更新失敗，請稍後再試。', 'danger')
        return redirect(url_for('main_bp.profile'))

    current_key = ''
    try:
        doc = user_ref.get()
        if doc.exists:
            current_key = doc.to_dict().get('gemini_api_key', '')
    except Exception as e:
        print(f"[Profile] Failed to fetch key: {e}")

    return render_template('profile.html', current_key=current_key)


@bp.route('/auth')
def auth():
    if 'user' in session:
        return redirect(url_for('main_bp.index'))
    return render_template('login.html', is_dev=is_dev_env())


@bp.route('/login')
def login():
    redirect_uri = url_for('main_bp.callback', _external=True)
    if redirect_uri.startswith('http://') and 'localhost' not in redirect_uri and '127.0.0.1' not in redirect_uri:
        redirect_uri = redirect_uri.replace('http://', 'https://', 1)
    # prompt='select_account'：每次點登入都強制顯示 Google 帳號選擇畫面，
    # 不走 SSO 免互動秒進（含登出後再登入）。
    return oauth.google.authorize_redirect(redirect_uri, prompt='select_account')


@bp.route('/dev_login')
def dev_login():
    if not is_dev_env():
        return "Access Denied", 403
    admin_email = get_admin_email() or os.environ.get('DEV_LOGIN_EMAIL', 'dev@localhost')
    session['user'] = {
        'name': 'Developer',
        'email': admin_email,
        'picture': 'https://via.placeholder.com/150'
    }
    session.modified = True
    return redirect('/')


@bp.route('/callback')
def callback():
    try:
        token = oauth.google.authorize_access_token()
        userinfo = token['userinfo']
        session['user'] = userinfo

        email = userinfo.get('email', '')
        display_name = userinfo.get('name', '')
        picture = userinfo.get('picture', '')

        # 確保用戶存在於 Firestore，取得白名單狀態
        status = ensure_user(email, display_name, picture)
        session['whitelist_status'] = status
        update_last_login(email)

        if status == 'approved':
            return redirect(url_for('project_bp.list_projects'))
        else:
            return redirect(url_for('main_bp.pending'))
    except Exception as e:
        # 不把內部例外細節暴露給使用者（資訊洩漏）；僅記錄於伺服器日誌。
        print(f"[Auth] OAuth callback 失敗: {e}", flush=True)
        flash('登入失敗，請重試。', 'danger')
        return redirect(url_for('main_bp.auth'))


@bp.route('/logout')
def logout():
    # 完整清除 session，避免 whitelist_status / _new_api_key 等殘留。
    session.clear()
    return redirect('/')


# ──────────────────────────────────────────────────────────────────────
# 以下路由為 Phase 3 預留 stub，目前回傳 503 Service Unavailable
# ──────────────────────────────────────────────────────────────────────

@bp.route('/submit_task', methods=['POST'])
@login_required
def submit_task():
    """Phase 3 重建：提交內容給 analysis-pipeline 服務。"""
    return jsonify({
        'error': '分析功能正在重建中（Phase 2/3），敬請期待。'
    }), 503


@bp.route('/task_status/<task_id>')
@login_required
def task_status(task_id):
    """Phase 3 重建：查詢 Firestore analyses/{id} 任務狀態。"""
    return jsonify({'error': '任務查詢功能正在重建中（Phase 3）。'}), 503


@bp.route('/stop_task/<task_id>', methods=['POST'])
@login_required
def stop_task(task_id):
    """Phase 3 重建：取消分析任務。"""
    return jsonify({'error': '停止功能正在重建中（Phase 3）。'}), 503


@bp.route('/download_project/<task_id>')
@login_required
def download_project(task_id):
    """Phase 3 重建：下載 Markdown 分析報告（取代舊 DOCX）。"""
    return jsonify({'error': '報告下載功能正在重建中（Phase 3）。'}), 503
