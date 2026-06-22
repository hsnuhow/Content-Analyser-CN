# -*- coding: utf-8 -*-
"""page_classify 頁面分類啟發式測試（crawler-service/page_classify）。

Characterization：鎖定從 crawler.HeadlessCrawler 抽出的 _looks_like_*_page 既有行為，
確保拆分零回歸。純字串判定，不觸發 driver / 網路。

可直接執行：python3 tests/test_page_classify.py
也相容 pytest：python3 -m pytest tests/test_page_classify.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crawler-service"))

import page_classify as pc  # noqa: E402


# ── 瀏覽器連線錯誤頁 ──
def test_browser_error_hit_short():
    assert pc.looks_like_browser_error_page("ERR_CONNECTION_REFUSED", "example.com") is True
    assert pc.looks_like_browser_error_page("無法連上這個網站，拒絕連線") is True
    assert pc.looks_like_browser_error_page("Just a moment...") is True  # Cloudflare 挑戰

def test_browser_error_long_content_not_error():
    # ≥1500 字幾乎不可能是錯誤頁，即使偶含特徵字
    long = "正文內容。" * 400 + "ERR_TIMED_OUT"
    assert len(long) >= 1500
    assert pc.looks_like_browser_error_page(long) is False

def test_browser_error_real_article_false():
    assert pc.looks_like_browser_error_page("這是一篇正常文章，談論健康與飲食。") is False

def test_browser_error_empty_false():
    assert pc.looks_like_browser_error_page("") is False
    assert pc.looks_like_browser_error_page(None) is False


# ── HTTP 錯誤頁 ──
def test_http_error_hit():
    assert pc.looks_like_http_error_page("403 Forbidden") is True
    assert pc.looks_like_http_error_page("頁面不存在") is True
    assert pc.looks_like_http_error_page("x", title="404 Not Found") is True

def test_http_error_clean_false():
    assert pc.looks_like_http_error_page("正常的文章內容，沒有錯誤碼。") is False


# ── 反爬封鎖/驗證頁 ──
def test_block_page_hit_short():
    assert pc.looks_like_block_page("請完成驗證，輸入驗證碼") is True
    assert pc.looks_like_block_page("Just a moment", "Attention Required") is True

def test_block_page_long_not_block():
    # 命中特徵但內容夠長（≥1200）→ 不判定為封鎖頁（避免長文誤殺）
    long = "我們偵測到一段文字。" + ("文章內容。" * 250)
    assert len(long.strip()) >= 1200
    assert pc.looks_like_block_page(long) is False

def test_block_page_clean_false():
    assert pc.looks_like_block_page("一般文章，無封鎖字樣。") is False


# ── 付費牆/不完整偵測（detect_paywall_incomplete）──
import crawler_config as _cc  # noqa: E402
_PW_M = _cc._PAYWALL_MARKERS_FLOOR
_PW_D = _cc._PAYWALL_DOMAINS_FLOOR


def test_paywall_cta_marker_hit():
    # A 型：天下 CTA 文字在內容裡 → paywall
    inc, reason = pc.detect_paywall_incomplete(
        "正文預覽…此篇為訂戶限定文章。查看訂閱方案", "https://www.cw.com.tw/article/1", _PW_M, _PW_D)
    assert inc is True and reason == "paywall"


def test_paywall_short_on_known_domain():
    # B 型：商周網域 + 內容過短（靜默截斷）→ paywall_short
    inc, reason = pc.detect_paywall_incomplete(
        "引言" * 20, "https://www.businessweekly.com.tw/magazine/x", _PW_M, _PW_D)
    assert inc is True and reason == "paywall_short"


def test_paywall_complete_not_flagged():
    # 一般完整文（無標記、非付費網域）不標
    inc, _ = pc.detect_paywall_incomplete("完整正文" * 400, "https://www.gq.com.tw/x", _PW_M, _PW_D)
    assert inc is False


def test_paywall_long_free_on_paywall_domain_not_flagged():
    # 付費牆網域的長免費文（>門檻）不應誤標（防 B 型誤判）
    inc, _ = pc.detect_paywall_incomplete(
        "完整正文" * 300, "https://www.businessweekly.com.tw/careers/blog/x", _PW_M, _PW_D)
    assert inc is False


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
    print(f"page_classify：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
