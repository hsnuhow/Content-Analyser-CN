# -*- coding: utf-8 -*-
"""text_clean 抽取後文字清理測試（crawler-service/text_clean）。

Characterization：鎖定從 crawler.HeadlessCrawler 抽出的 _clean_text /
_trim_trailing_boilerplate 既有行為，確保拆分零回歸。純字串處理，無 driver / 網路。

可直接執行：python3 tests/test_text_clean.py
也相容 pytest：python3 -m pytest tests/test_text_clean.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crawler-service"))

import text_clean as tc  # noqa: E402


# ── clean_text：去空白行 + 去 <4 字短行 + 雙換行連接 ──
def test_clean_text_drops_short_lines():
    # 'abc'(3字) 與 '短'(1字) < 4 → 丟棄；'正常的一行字'(6字) 保留
    assert tc.clean_text("abc\n短\n正常的一行字\n\n  ") == "正常的一行字"

def test_clean_text_double_newline_join():
    assert tc.clean_text("第一段內容\n第二段內容") == "第一段內容\n\n第二段內容"

def test_clean_text_keeps_4char_line():
    # 4 字即可成行（一句短句如「不知道。」）
    assert tc.clean_text("不知道。") == "不知道。"

def test_clean_text_empty():
    assert tc.clean_text("") == ""
    assert tc.clean_text(None) == ""


# ── trim_trailing_boilerplate：達 min_keep 後遇樣板截斷；之前不動 ──
def test_trim_cuts_after_min_keep():
    body = "正文" * 100  # 200 字 ≥ 150
    out = tc.trim_trailing_boilerplate(body + "\n版權所有 © 本站")
    assert "版權所有" not in out
    assert len(out) >= 200

def test_trim_keeps_short_content_even_with_marker():
    # 累積正文 < min_keep（150）時即使含樣板也不砍（避免短文誤殺）
    out = tc.trim_trailing_boilerplate("很短的內容\n版權所有")
    assert "版權所有" in out

def test_trim_no_marker_unchanged():
    body = "純正文沒有任何樣板字串。" * 30
    assert tc.trim_trailing_boilerplate(body).strip() == body.strip()

def test_trim_empty():
    assert tc.trim_trailing_boilerplate("") == ""

def test_trim_log_fn_called_on_cut():
    calls = []
    body = "正文" * 100 + "\n立即下載 APP"
    tc.trim_trailing_boilerplate(body, log_fn=lambda m: calls.append(m))
    assert len(calls) == 1 and "截斷" in calls[0]


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
    print(f"text_clean：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
