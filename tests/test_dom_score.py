# -*- coding: utf-8 -*-
"""dom_score DOM 節點評分測試（crawler-service/dom_score）。

Characterization：鎖定從 crawler.HeadlessCrawler 抽出的 _calculate_*/_looks_like_*/_css_path
等純函式既有行為，確保拆分零回歸。需 bs4（與 crawler 同環境）。

可直接執行：python3 tests/test_dom_score.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crawler-service"))

try:
    from bs4 import BeautifulSoup
except Exception:
    print("dom_score：跳過（本機無 bs4）")
    sys.exit(0)

import dom_score as ds  # noqa: E402

ARTICLE_HTML = """
<html><body>
<div id="main" class="article-content">
  <h1>標題</h1>
  <p>這是一段有意義的正文內容，談論健康與飲食，標點密度適中，提供讀者實用觀點。確實如此。</p>
  <p>第二段也是正文，內容充實，補充細節與例證，讓整篇文章更完整可讀。值得一讀再三。</p>
  <p>第三段繼續延伸主題，從不同角度切入，帶出更深入的分析與結論。沒有問題喔。</p>
</div>
<aside class="sidebar widget"><p>側欄小工具</p></aside>
<div id="list" class="card-list"><li>項一</li><li>項二</li><li>項三</li><li>項四</li><li>項五</li><li>項六</li></div>
</body></html>
"""


def _soup():
    return BeautifulSoup(ARTICLE_HTML, "html.parser")


def test_chinese_ratio():
    assert ds.calculate_chinese_ratio("全部中文") == 1.0
    assert ds.calculate_chinese_ratio("abcd") == 0.0
    assert ds.calculate_chinese_ratio("") == 0.0
    r = ds.calculate_chinese_ratio("中文abc")
    assert 0.0 < r < 1.0

def test_visual_weight_main_vs_sidebar():
    s = _soup()
    main = s.find(id="main")
    side = s.find("aside")
    # main/content/article 加分；sidebar/widget/aside 扣分 → main 權重 > sidebar
    assert ds.calculate_visual_weight(main, s) > ds.calculate_visual_weight(side, s)
    assert ds.calculate_visual_weight(None, s) == 1.0

def test_paragraph_quality_positive():
    s = _soup()
    main = s.find(id="main")
    # 三段達標正文 → 品質分 > 0
    assert ds.calculate_paragraph_quality(main) > 0.0
    # 無 p 的節點 → 0
    assert ds.calculate_paragraph_quality(s.find("aside")) == 0.0

def test_looks_like_listing_block_by_li():
    s = _soup()
    # #list 含 6 個「直接子」<li>（recursive=False，>5）→ 判為列表區塊
    assert ds.looks_like_listing_block(s.find(id="list")) is True
    # 正文 div → 非列表
    assert ds.looks_like_listing_block(s.find(id="main")) is False

def test_looks_like_cookie_banner():
    assert ds.looks_like_cookie_banner("we use cookies for consent and gdpr compliance") is True
    assert ds.looks_like_cookie_banner("一般文章內容，沒有同意字樣") is False
    assert ds.looks_like_cookie_banner("") is False

def test_node_score_article_beats_sidebar():
    s = _soup()
    main_score, main_breakdown = ds.calculate_node_score(s.find(id="main"), s)
    # 正文節點得分 > 0 且有分項
    assert main_score > 0.0
    assert set(main_breakdown) >= {"text_length", "paragraph_quality", "link_density",
                                   "dom_depth", "visual_weight", "chinese_ratio"}
    # 列表 #list 被 listing 判定 → 0 分
    list_score, _ = ds.calculate_node_score(s.find(id="list"), s)
    assert list_score == 0.0

def test_node_score_too_short():
    s = BeautifulSoup("<div><p>短</p></div>", "html.parser")
    score, _ = ds.calculate_node_score(s.find("div"), s)
    assert score == 0.0  # <100 字直接 0

def test_css_path_and_depth():
    s = _soup()
    main = s.find(id="main")
    path = ds.css_path(main)
    assert "main" in path and "#main" in path  # 含 id
    assert ds.get_element_depth(main) >= 2

def test_confidence_monotonic():
    # 分數越高、margin 越大 → 信心越高
    low = ds.calculate_confidence(400, 350, None)
    high = ds.calculate_confidence(1600, 200, None)
    assert 0.0 <= low <= 1.0 and 0.0 <= high <= 1.0
    assert high > low


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
    print(f"dom_score：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
