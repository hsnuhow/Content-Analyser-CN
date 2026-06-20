# -*- coding: utf-8 -*-
"""FakeFirestore 測試替身自我測試 + N+1 查詢邏輯驗證。

同時示範「未來測 db 相依邏輯」的寫法（注入 FakeFirestore，不碰真 Firestore）。
可直接執行：python3 tests/test_fakes.py　｜　相容 pytest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fakes import FakeFirestore  # noqa: E402


def _seed():
    # me=a@x.com：p1 我是 owner、p2 我是成員、p3 與我無關、p4 我是 owner 但無 member_emails 欄
    return FakeFirestore({'projects': {
        'p1': {'owner': 'a@x.com', 'members': {}, 'member_emails': []},
        'p2': {'owner': 'b@x.com', 'members': {'a@x.com': 'editor'}, 'member_emails': ['a@x.com']},
        'p3': {'owner': 'c@x.com', 'members': {'d@x.com': 'viewer'}, 'member_emails': ['d@x.com']},
        'p4': {'owner': 'a@x.com', 'members': {}},   # 缺 member_emails（backfill 前）
    }})


# ── FakeFirestore 基本行為 ──
def test_where_equals():
    db = _seed()
    ids = sorted(d.id for d in db.collection('projects').where('owner', '==', 'a@x.com').stream())
    assert ids == ['p1', 'p4']

def test_where_array_contains():
    db = _seed()
    ids = sorted(d.id for d in db.collection('projects').where('member_emails', 'array_contains', 'a@x.com').stream())
    assert ids == ['p2']

def test_array_contains_missing_field_safe():
    # p4 沒有 member_emails 欄 → array_contains 不應炸、不應命中
    db = _seed()
    ids = [d.id for d in db.collection('projects').where('member_emails', 'array_contains', 'zzz@x.com').stream()]
    assert ids == []

def test_document_set_get_update():
    db = _seed()
    ref = db.collection('projects').document('p5')
    ref.set({'owner': 'a@x.com', 'members': {}})
    assert ref.get().exists and ref.get().to_dict()['owner'] == 'a@x.com'
    ref.update({'member_emails': ['x@x.com']})
    assert ref.get().to_dict()['member_emails'] == ['x@x.com']

def test_missing_doc_not_exists():
    db = _seed()
    assert db.collection('projects').document('nope').get().exists is False


# ── N+1 查詢邏輯（list_projects 非 admin 路徑）──
def test_list_projects_query_logic():
    """模擬 list_projects 非 admin：owner 等值查 + member_emails array_contains，合併去重。
    驗證：a@x.com 應看到 p1(owner)、p4(owner，即使缺 member_emails)、p2(member)，但不含 p3。"""
    db = _seed()
    me = 'a@x.com'
    seen, ids = set(), []
    for d in db.collection('projects').where('owner', '==', me).stream():
        if d.id not in seen:
            seen.add(d.id); ids.append(d.id)
    for d in db.collection('projects').where('member_emails', 'array_contains', me).stream():
        if d.id not in seen:
            seen.add(d.id); ids.append(d.id)
    assert sorted(ids) == ['p1', 'p2', 'p4'], f'得到 {sorted(ids)}'
    assert 'p3' not in ids   # 與我無關的專案不外洩


def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn(); passed += 1
        except AssertionError as e:
            failed += 1; print(f"  ✗ {name}  {e}")
        except Exception as e:
            failed += 1; print(f"  ✗ {name}  (例外) {e}")
    print(f"fakes/N+1：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
