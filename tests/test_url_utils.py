# -*- coding: utf-8 -*-
"""url_utils characterization 測試（app/url_utils.py）。

鎖住自 project_routes 抽出的 URL 正規化 / 清單解析行為（去重判同、追蹤參數剝除、
黏在一起的網址切分）。純 urllib/re，無 db。

可直接執行：python3 tests/test_url_utils.py　｜　相容 pytest
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import url_utils as u  # noqa: E402


# ── _url_key 正規化 ──
def test_strips_tracking_params():
    assert u._url_key('https://x.com/a?utm_source=fb&id=3') == 'https://x.com/a?id=3'
    assert u._url_key('https://x.com/a?fbclid=zzz') == 'https://x.com/a'

def test_lowercases_host_keeps_path_case():
    assert u._url_key('https://EXAMPLE.com/Path') == 'https://example.com/Path'

def test_removes_trailing_slash_and_fragment():
    assert u._url_key('https://x.com/a/#sec') == 'https://x.com/a'

def test_keeps_non_tracking_query_sorted():
    assert u._url_key('https://x.com/a?b=2&a=1') == 'https://x.com/a?a=1&b=2'

def test_default_port_normalized():
    assert u._url_key('https://x.com:443/a') == 'https://x.com/a'
    assert u._url_key('http://x.com:80/a') == 'http://x.com/a'

def test_same_page_different_tracking_same_key():
    assert u._url_key('https://x.com/p?utm_source=a') == u._url_key('https://x.com/p?gclid=b')

def test_empty_and_garbage():
    assert u._url_key('') == ''
    assert u._url_key('not a url') == 'not a url'


# ── parse_url_list 容錯解析 + 去重 ──
def test_parse_dedup_by_key():
    out = u.parse_url_list('https://a.com/1 https://a.com/1?utm_source=x')
    assert out == ['https://a.com/1']

def test_parse_glued_urls():
    # 兩個網址黏成一坨（無分隔）→ 用 lookahead 切開
    out = u.parse_url_list('https://a.com/1https://b.com/2')
    assert out == ['https://a.com/1', 'https://b.com/2']

def test_parse_encoded_newlines():
    out = u.parse_url_list('https://a.com/1%0Ahttps://b.com/2')
    assert out == ['https://a.com/1', 'https://b.com/2']

def test_parse_ignores_non_http():
    out = u.parse_url_list('ftp://x.com hello https://ok.com')
    assert out == ['https://ok.com']

def test_parse_empty():
    assert u.parse_url_list('') == []
    assert u.parse_url_list(None) == []


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
    print(f"url_utils：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
