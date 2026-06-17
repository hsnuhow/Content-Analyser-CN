# -*- coding: utf-8 -*-
"""
知識庫專家儲存層（Firestore kb_experts）。

模型 A：每個啟用的專家 = 報告頁可產生的一種延伸報告。
doc_id = slug（[a-z0-9-]，建立後不可改；改 slug＝刪除後重建，使 derived_reports 鍵穩定）。
欄位：slug / label / prompt / playbook / enabled / order / created_at / updated_at。
（Phase 2 再加 documents 子集合 + kb_chunks 向量庫。）
"""
import re

from firebase_admin import firestore

from .services import db
from .kb_seed import DEFAULT_EXPERTS

COLLECTION = 'kb_experts'
_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')


def slug_ok(slug: str) -> bool:
    return bool(slug and _SLUG_RE.match(slug))


def _col():
    return db.collection(COLLECTION)


def seed_default_experts() -> int:
    """若 kb_experts 為空，寫入預設三專家。回傳新建數量（已有資料則 0，冪等）。"""
    try:
        existing = list(_col().limit(1).stream())
        if existing:
            return 0
        n = 0
        for e in DEFAULT_EXPERTS:
            _col().document(e['slug']).set({
                'slug': e['slug'], 'label': e['label'],
                'prompt': e['prompt'], 'playbook': e['playbook'],
                'enabled': True, 'order': e.get('order', 99),
                'created_at': firestore.SERVER_TIMESTAMP,
                'updated_at': firestore.SERVER_TIMESTAMP,
            })
            n += 1
        return n
    except Exception as e:
        print(f"[kb] seed 失敗：{e}", flush=True)
        return 0


def list_experts() -> list:
    """全部專家，依 order 排序。"""
    try:
        docs = [d.to_dict() | {'id': d.id} for d in _col().stream()]
    except Exception as e:
        print(f"[kb] list 失敗：{e}", flush=True)
        return []
    docs.sort(key=lambda x: (x.get('order', 99), x.get('label', '')))
    return docs


def list_enabled_experts() -> list:
    """啟用的專家（供生成延伸報告），依 order 排序。"""
    return [e for e in list_experts() if e.get('enabled')]


def get_expert(slug: str) -> dict | None:
    try:
        doc = _col().document(slug).get()
        return (doc.to_dict() | {'id': doc.id}) if doc.exists else None
    except Exception:
        return None


def create_expert(slug: str, label: str, prompt: str, playbook: str,
                   enabled: bool = True, order: int = 99) -> tuple:
    """建立專家。回 (ok, msg)。slug 須合法且未存在。"""
    slug = (slug or '').strip().lower()
    if not slug_ok(slug):
        return False, 'slug 格式錯誤（限小寫英數與連字號，1–40 字）'
    if not (label or '').strip():
        return False, '請填寫顯示名稱'
    if _col().document(slug).get().exists:
        return False, f'slug「{slug}」已存在'
    _col().document(slug).set({
        'slug': slug, 'label': label.strip(),
        'prompt': prompt or '', 'playbook': playbook or '',
        'enabled': bool(enabled), 'order': int(order),
        'created_at': firestore.SERVER_TIMESTAMP,
        'updated_at': firestore.SERVER_TIMESTAMP,
    })
    return True, '已建立'


def update_expert(slug: str, label: str = None, prompt: str = None,
                  playbook: str = None, enabled: bool = None,
                  order: int = None) -> tuple:
    """更新專家（slug 不可改）。只更新有給的欄位。回 (ok, msg)。"""
    ref = _col().document(slug)
    if not ref.get().exists:
        return False, '找不到此專家'
    upd = {'updated_at': firestore.SERVER_TIMESTAMP}
    if label is not None:
        if not label.strip():
            return False, '顯示名稱不可空白'
        upd['label'] = label.strip()
    if prompt is not None:
        upd['prompt'] = prompt
    if playbook is not None:
        upd['playbook'] = playbook
    if enabled is not None:
        upd['enabled'] = bool(enabled)
    if order is not None:
        upd['order'] = int(order)
    ref.update(upd)
    return True, '已更新'


def delete_expert(slug: str) -> tuple:
    """刪除專家（不影響既有已產生的 derived_reports，那是歷史快照）。回 (ok, msg)。"""
    ref = _col().document(slug)
    if not ref.get().exists:
        return False, '找不到此專家'
    ref.delete()
    return True, '已刪除'
