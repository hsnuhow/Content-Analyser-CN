import os
import signal
from functools import wraps
from flask import Blueprint, render_template, session, redirect, url_for, request, flash
from .services import get_secret, set_secret

bp = Blueprint('admin_bp', __name__, url_prefix='/admin')

ADMIN_EMAIL = "how.penguin@gmail.com"
ALLOWED_SECRETS = ['SYSTEM_GEMINI_KEY']

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        if not user:
            return redirect(url_for('main_bp.auth'))
            
        user_email = user.get('email', '').lower()
        if user_email != ADMIN_EMAIL.lower():
            print(f"[Admin] Access Denied. User: {user_email}, Required: {ADMIN_EMAIL}")
            return "Access Denied: You are not an administrator.", 403
            
        return f(*args, **kwargs)
    return decorated_function

@bp.route('/')
@admin_required
def admin_dashboard():
    system_key = get_secret('SYSTEM_GEMINI_KEY')
    key_status = "Configured" if system_key and system_key != "INITIAL_PLACEHOLDER_KEY" else "Not Configured"
    
    return render_template('admin_dashboard.html', user=session.get('user'), key_status=key_status)

@bp.route('/update_secrets', methods=['POST'])
@admin_required
def update_secrets():
    key_name = request.form.get('key_name')
    key_value = request.form.get('key_value')
    
    if not key_name or not key_value:
        flash('Error: Missing key name or value.', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))
        
    if key_name not in ALLOWED_SECRETS:
        flash(f'Error: Updating secret "{key_name}" is not allowed via this panel.', 'danger')
        return redirect(url_for('admin_bp.admin_dashboard'))

    success = set_secret(key_name, key_value)
    
    if success:
        flash(f'Successfully updated {key_name}. The new key will be used for system tasks.', 'success')
    else:
        flash(f'Failed to update {key_name}. Please check system logs.', 'danger')

    return redirect(url_for('admin_bp.admin_dashboard'))

@bp.route('/force_kill_crawler', methods=['POST'])
@admin_required
def force_kill_crawler():
    """Emergency Stop.

    爬蟲已拆分為獨立的 Cloud Run 服務 (content-crawler)，Chrome 不再於主程式容器內執行，
    因此這裡不再 pkill chrome。要中止進行中的任務，請於專案頁使用「停止」(stop_task)；
    要重置爬蟲服務，請於 Cloud Run 重啟 content-crawler 服務。
    """
    flash('爬蟲已是獨立服務 (content-crawler)，主程式不再執行 Chrome。'
          '請改用專案頁的「停止」按鈕中止任務，或於 Cloud Run 重啟爬蟲服務。', 'info')
    return redirect(url_for('admin_bp.admin_dashboard'))
