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
    """Emergency Stop: Force kill all chrome processes and reset crawler instance"""
    try:
        from .worker import CURRENT_CRAWLER_INSTANCE
        if CURRENT_CRAWLER_INSTANCE:
            print("[Admin] Force closing active crawler instance...")
            try:
                CURRENT_CRAWLER_INSTANCE.close()
            except Exception as e:
                print(f"[Admin] Error closing instance: {e}")
        
        # Force Kill System Processes (Linux)
        print("[Admin] Executing system pkill...")
        os.system("pkill -9 chrome")
        os.system("pkill -9 chromedriver")
        
        flash('Emergency Stop executed. Chrome processes killed.', 'warning')
    except Exception as e:
        flash(f'Error executing Force Kill: {e}', 'danger')
        
    return redirect(url_for('admin_bp.admin_dashboard'))
