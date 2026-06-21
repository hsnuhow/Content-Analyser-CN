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
# 註：此 package __init__ 只放 Blueprint + 共用 helper；各領域路由邏輯與其 client/store
# import 都在子模組（projects/analysis/datasets/discovery）。本檔僅 import 自己 helper 用得到的。
from functools import wraps
from flask import (Blueprint, session, redirect, url_for, flash, abort)
from firebase_admin import firestore

from ..services import db, get_admin_email
from ..auth_guards import refresh_whitelist_status

bp = Blueprint('project_bp', __name__, url_prefix='/projects')

# ──────────────────────────────────────────────────────────────────────
# 輔助函式
# ──────────────────────────────────────────────────────────────────────

def current_user_email() -> str:
    return session.get('user', {}).get('email', '')


# re-export：admin_routes 靠 `from .project_routes import _load_dataset_items` 取用，故此處保留。
# （其餘 url_utils / datasets_store 名稱由各子模組直接 import，不需在此 re-export。）
from ..datasets_store import _load_dataset_items  # noqa: F401

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

# ── 領域子模組：⚠️ 這四行是「side-effect import」——import 時各子模組會套用自己的 @bp.route
#    完成路由註冊（須在 bp + 共用 helper 定義之後才 import）。看似未使用，但**絕不可刪**
#    （刪掉 = 所有 project 路由消失 404）；autoflake/自動清未用 import 時務必略過。──
from . import projects   # noqa: E402,F401
from . import analysis   # noqa: E402,F401
from . import datasets   # noqa: E402,F401
from . import discovery  # noqa: E402,F401
