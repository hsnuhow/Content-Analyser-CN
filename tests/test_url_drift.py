# -*- coding: utf-8 -*-
"""url_drift 自動轉移偵測測試（crawler-service/url_drift）。

鎖定「強化版轉移偵測」行為：抓真轉移（跨站/登入牆/首頁/錯誤頁），但不誤判合法 redirect
（http→https、尾斜線、m. 行動版、locale 前綴）。純函式，無 driver / 網路。

可直接執行：python3 tests/test_url_drift.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crawler-service"))

import url_drift as ud  # noqa: E402


def _t(url, final):
    return ud.detect_auto_transfer(url, final)[0]


# ── 合法 redirect：不可誤判為轉移 ──
def test_no_change():
    assert _t("https://x.com/article/123", "https://x.com/article/123") is False

def test_http_to_https():
    assert _t("http://x.com/a/1", "https://x.com/a/1") is False

def test_trailing_slash():
    assert _t("https://x.com/a/1", "https://x.com/a/1/") is False

def test_mobile_subdomain():
    assert _t("https://www.x.com/a/1", "https://m.x.com/a/1") is False

def test_locale_prefix():
    # path 變了（加 /en/）但非牆/首頁 → 不判轉移
    assert _t("https://x.com/a/1", "https://x.com/en/a/1") is False

def test_tracking_strip_query():
    assert _t("https://x.com/a/1?utm_source=fb", "https://x.com/a/1") is False


# ── 真轉移：須抓到 ──
def test_cross_domain():
    assert _t("https://en.wikipedia.org/wiki/X", "https://www.google.com/") is True

def test_login_subdomain():
    assert _t("https://x.com/article/123", "https://auth.x.com/login") is True

def test_redirect_to_homepage():
    assert _t("https://x.com/article/123", "https://x.com/") is True

def test_login_path():
    assert _t("https://x.com/article/123", "https://x.com/login?next=/article/123") is True

def test_error_path():
    assert _t("https://x.com/article/123", "https://x.com/error/blocked") is True


# ── reason 內容 + 邊界 ──
def test_reason_present():
    t, reason = ud.detect_auto_transfer("https://x.com/a/1", "https://y.com/")
    assert t and "y.com" in reason

def test_empty_final():
    assert _t("https://x.com/a/1", "") is False

def test_reg_host():
    assert ud.reg_host("www.x.com") == "x.com"
    assert ud.reg_host("a.b.x.com") == "x.com"


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
    print(f"url_drift：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
