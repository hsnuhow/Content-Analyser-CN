# -*- coding: utf-8 -*-
"""資料集 items 子集合存取層（自 project_routes.py 抽出）。

projects/{pid}/datasets/{did}/items 子集合的讀寫：_seq 排序、並發安全 append（交易預約
連續序號）、向後相容舊內嵌 items 格式。依賴 services.db、firestore、url_utils._url_key。
project_routes 由此 import 並 re-export（admin_routes 仍可 from project_routes import _load_dataset_items）。
"""
from firebase_admin import firestore

from .services import db
from .url_utils import _url_key


def _items_ref(pid: str, did: str):
    return (db.collection('projects').document(pid)
            .collection('datasets').document(did).collection('items'))


def _load_dataset_items(pid: str, did: str) -> list:
    try:
        items = []
        for d in _items_ref(pid, did).order_by('_seq').stream():
            it = d.to_dict()
            it['_id'] = d.id   # 供單篇刪除引用
            items.append(it)
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
        batch.set(ref.document(), {**it, 'url_key': _url_key(it.get('url', '')), '_seq': seq})
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


def _append_urls_to_draft(pid: str, did: str, urls: list):
    """把 urls 併入現有『草稿』資料集（去重、append pending items、更新 source_urls/計數）。
    回新增筆數；目標不存在或非草稿回 None。"""
    ds_ref = (db.collection('projects').document(pid)
              .collection('datasets').document(did))
    doc = ds_ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    if data.get('status') != 'draft':
        return None
    existing = _load_dataset_items(pid, did)
    have = {it.get('url_key') or _url_key(it.get('url', '')) for it in existing}
    fresh = [u for u in urls if _url_key(u) not in have]
    fresh = list(dict.fromkeys(fresh))
    if fresh:
        _save_dataset_items(pid, did, [{'url': u, 'status': 'pending'} for u in fresh],
                            append=True)
        merged_urls = list(dict.fromkeys((data.get('source_urls') or []) + fresh))
        ds_ref.update({'source_urls': merged_urls,
                       'item_count': len(merged_urls),
                       'updated_at': firestore.SERVER_TIMESTAMP})
    return len(fresh)


def _replace_items_by_url(pid: str, did: str, urls_set: set, new_items: list) -> None:
    """recrawl-failed：刪除 url 在 urls_set 的舊 item，再 append new_items（保留已成功項）。"""
    ref = _items_ref(pid, did)
    for d in ref.stream():
        if (d.to_dict() or {}).get('url') in urls_set:
            d.reference.delete()
    _save_dataset_items(pid, did, new_items, append=True)
