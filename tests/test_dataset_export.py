# -*- coding: utf-8 -*-
"""dataset_export characterization 測試（app/dataset_export.py）。

鎖住自 project_routes 抽出的匯出序列化行為。純函式、無 db。

可直接執行：python3 tests/test_dataset_export.py　｜　相容 pytest
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import dataset_export as de  # noqa: E402


def _ds():
    return {
        'name': '循環扇', 'item_count': 3,
        'items': [
            {'status': 'success', 'title': '評測A', 'url': 'https://a.com', 'length': 1200, 'content': '內文A'},
            {'status': 'success', 'title': '', 'url': 'https://b.com', 'length': 800, 'content': '內文B'},
            {'status': 'failed', 'url': 'https://c.com', 'error': '逾時'},
        ],
    }


# ── Markdown ──
def test_md_header_and_count():
    md = de._dataset_to_markdown(_ds())
    assert md.startswith('# 循環扇')
    assert '共 3 個網址，成功 2 篇' in md

def test_md_success_items_rendered():
    md = de._dataset_to_markdown(_ds())
    assert '## 評測A' in md and '內文A' in md
    assert '## (無標題)' in md          # 空標題 fallback

def test_md_failed_section():
    md = de._dataset_to_markdown(_ds())
    assert '## 未成功項目' in md
    assert '[failed] https://c.com — 逾時' in md

def test_md_success_excludes_empty_content():
    ds = {'name': 'x', 'items': [{'status': 'success', 'url': 'u', 'content': ''}]}
    md = de._dataset_to_markdown(ds)
    assert '成功 0 篇' in md and '## 未成功項目' in md   # 無內文不算成功


# ── JSON ──
def test_json_structure_and_counts():
    j = de._dataset_to_json(_ds())
    assert j['dataset'] == '循環扇'
    assert j['item_count'] == 3
    assert j['succeeded'] == 2
    assert len(j['items']) == 3                       # 含失敗項
    assert j['items'][2]['status'] == 'failed' and j['items'][2]['error'] == '逾時'

def test_json_empty_dataset():
    j = de._dataset_to_json({})
    assert j['item_count'] == 0 and j['succeeded'] == 0 and j['items'] == []


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
    print(f"dataset_export：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
