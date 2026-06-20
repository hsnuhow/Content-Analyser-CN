# -*- coding: utf-8 -*-
"""json_utils characterization test（analysis-service/json_utils.py）。

鎖住先前散落 4 份（llm_path/synthesis/denoise/image_report）的 LLM-JSON 清理行為，
作為安全去重的回歸保護。純函式、無外部依賴。

可直接執行：python3 tests/test_json_utils.py　｜　相容 pytest
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis-service"))

import json_utils as J  # noqa: E402


# ── clean_json_str ──
def test_plain_object():
    assert J.clean_json_str('{"a":1}') == '{"a":1}'

def test_fenced_json():
    assert J.clean_json_str('```json\n{"a":1}\n```') == '{"a":1}'

def test_fenced_plain():
    assert J.clean_json_str('```\n{"a":1}\n```') == '{"a":1}'

def test_prose_around_object():
    assert J.clean_json_str('這是結果：\n{"a":1}\n以上。') == '{"a":1}'

def test_nested_object_outermost():
    src = '{"a":{"b":2},"c":[1,2]}'
    assert J.clean_json_str("```json\n" + src + "\n```") == src

def test_leading_trailing_whitespace():
    assert J.clean_json_str('   \n  {"a":1}  \n ') == '{"a":1}'

def test_no_object_returns_cleaned():
    # 找不到 {...} → 回清理後原字串（去 fence + strip）
    assert J.clean_json_str('```json\nnot json here\n```') == 'not json here'

def test_none_input():
    assert J.clean_json_str(None) == ''

def test_empty_input():
    assert J.clean_json_str('') == ''

def test_multiline_object():
    src = '{\n  "a": 1,\n  "b": "x"\n}'
    out = J.clean_json_str("```json\n" + src + "\n```")
    assert json.loads(out) == {"a": 1, "b": "x"}


# ── parse_json_obj ──
def test_parse_valid():
    assert J.parse_json_obj('```json\n{"k":"v"}\n```') == {"k": "v"}

def test_parse_invalid_default_none():
    assert J.parse_json_obj('not json at all {') is None

def test_parse_invalid_custom_fallback():
    fb = {"raw": "x"}
    assert J.parse_json_obj('garbage', fallback=fb) is fb

def test_parse_array_ok():
    # 最外層 {...} 抽取：陣列被包在物件內才會被抓；純陣列無 {} → 清理後 json.loads
    assert J.parse_json_obj('{"items":[1,2,3]}') == {"items": [1, 2, 3]}


def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {name}  {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {name}  (例外) {e}")
    print(f"json_utils：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
