import os
import threading
import uuid
from functools import wraps
from flask import Blueprint, render_template, request, jsonify, current_app, session, redirect, url_for, flash, send_file
from firebase_admin import firestore 
from .worker import analysis_pipeline
from .services import db
from . import oauth
from .export_utils import generate_project_docx # Import export utility

bp = Blueprint('main_bp', __name__)

ADMIN_EMAIL = "how.penguin@gmail.com"

def is_dev_env():
    return os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('FLASK_ENV') == 'development'

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            if is_dev_env():
                print(f"[Debug] login_required: Auto-login for dev environment.")
                session['user'] = {
                    'name': 'Developer',
                    'email': ADMIN_EMAIL, 
                    'picture': 'https://via.placeholder.com/150'
                }
                return f(*args, **kwargs)
            
            print(f"[Debug] login_required: User not found in session. Redirecting to auth.")
            return redirect(url_for('main_bp.auth'))
        return f(*args, **kwargs)
    return decorated_function

# Inject user info into all templates
@bp.context_processor
def inject_user():
    user = session.get('user')
    is_admin = False
    if user:
        is_admin = user.get('email', '').lower() == ADMIN_EMAIL.lower()
    return dict(user=user, is_admin=is_admin)

@bp.route('/debug')
def debug():
    return jsonify({
        'session_user': session.get('user'),
        'is_dev_env': is_dev_env(),
        'flask_debug': os.environ.get('FLASK_DEBUG')
    })

@bp.route('/')
@login_required
def index():
    return render_template('index.html')

@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_email = session['user']['email']
    user_ref = db.collection('users').document(user_email)

    if request.method == 'POST':
        api_key = request.form.get('gemini_api_key')
        try:
            user_ref.set({
                'gemini_api_key': api_key,
                'updated_at': firestore.SERVER_TIMESTAMP 
            }, merge=True)
            flash('Your API Key has been updated successfully!', 'success')
        except Exception as e:
            print(f"[Profile] Failed to update key: {e}")
            flash('Failed to update API Key. Please try again.', 'danger')
            
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
    return oauth.google.authorize_redirect(redirect_uri)

@bp.route('/dev_login')
def dev_login():
    if not is_dev_env():
        return "Access Denied", 403
    session['user'] = {
        'name': 'Developer',
        'email': ADMIN_EMAIL,
        'picture': 'https://via.placeholder.com/150'
    }
    session.modified = True
    return redirect('/')

@bp.route('/callback')
def callback():
    try:
        token = oauth.google.authorize_access_token()
        session['user'] = token['userinfo']
        return redirect('/')
    except Exception as e:
        return f"Login failed: {e}"

@bp.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')

@bp.route('/submit_task', methods=['POST'])
@login_required
def submit_task():
    data = request.get_json()
    if not data or 'urls' not in data:
        return jsonify({'error': 'Missing URLs'}), 400

    user_email = session['user']['email']
    report_title = data.get('report_title', 'Untitled Project')
    
    try:
        project_ref = db.collection('users').document(user_email).collection('projects').document()
        project_id = project_ref.id
        
        project_ref.set({
            'created_at': firestore.SERVER_TIMESTAMP,
            'status': 'pending',
            'progress': 0,
            'log': 'Project created',
            'report_title': report_title,
            'input_urls': data.get('urls'),
            'use_gemini': data.get('use_gemini', False)
        })
        
        thread = threading.Thread(target=analysis_pipeline, args=(project_id, user_email, data, current_app._get_current_object()))
        thread.start()

        return jsonify({'task_id': project_id})
    except Exception as e:
        print(f"[Submit] Error creating project: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/task_status/<task_id>')
@login_required
def task_status(task_id):
    user_email = session['user']['email']
    try:
        project_ref = db.collection('users').document(user_email).collection('projects').document(task_id)
        doc = project_ref.get()
        
        if not doc.exists:
            return jsonify({'error': 'Project not found'}), 404
            
        data = doc.to_dict()
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/download_project/<task_id>')
@login_required
def download_project(task_id):
    user_email = session['user']['email']
    file_stream, filename = generate_project_docx(user_email, task_id)
    
    if not file_stream:
        return f"Error: {filename}", 404
        
    return send_file(
        file_stream,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )

# [Feature] 新增停止任務的路由
@bp.route('/stop_task/<task_id>', methods=['POST'])
@login_required
def stop_task(task_id):
    user_email = session['user']['email']
    try:
        project_ref = db.collection('users').document(user_email).collection('projects').document(task_id)
        # 更新狀態為 cancelled，Worker 下一輪檢查時會自動停止
        project_ref.update({
            'status': 'cancelled',
            'log': 'User requested cancellation.'
        })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
