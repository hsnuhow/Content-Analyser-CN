# -*- coding: utf-8 -*-
"""dom_parse HTML 解析/結構判定測試（crawler-service/dom_parse）。

Characterization：鎖定從 crawler.HeadlessCrawler 抽出的 JSON-LD / block payload 抽取、
meta 後備、列表頁判定、CMP 移除既有行為。需 bs4。

可直接執行：python3 tests/test_dom_parse.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crawler-service"))

try:
    from bs4 import BeautifulSoup
except Exception:
    print("dom_parse：跳過（本機無 bs4）")
    sys.exit(0)

import dom_parse as dp  # noqa: E402

_BODY = "這是一篇文章的正文內容，" * 30  # >200 字


def test_json_ld_article_body():
    html = f'<html><head><script type="application/ld+json">{{"@type":"NewsArticle","articleBody":"{_BODY}"}}</script></head><body></body></html>'
    out = dp.extract_from_json_ld(html)
    assert len(out) >= 200 and "正文內容" in out

def test_json_ld_graph():
    html = f'<script type="application/ld+json">{{"@graph":[{{"@type":"WebPage"}},{{"@type":"Article","articleBody":"{_BODY}"}}]}}</script>'
    assert "正文內容" in dp.extract_from_json_ld(html)

def test_json_ld_none():
    assert dp.extract_from_json_ld("<html><body>無 ld</body></html>") == ""

def test_block_payload_rsc():
    # 格式1 ["p","..."] + 格式2 RSC
    html = '<script>["p","這是第一段超過十個字的內容文字。"]</script><script>["$","p","k",{"children":"這是第二段也夠長的內容文字喔。"}]</script>'
    out = dp.extract_from_block_payload(html)
    assert "第一段" in out and "第二段" in out

def test_block_payload_dedup():
    html = '<script>["p","重複的段落內容超過十個字喔喔。"]["p","重複的段落內容超過十個字喔喔。"]</script>'
    out = dp.extract_from_block_payload(html)
    assert out.count("重複的段落") == 1  # 去重

def test_apply_meta_fallback():
    html = '<html><head><meta property="og:description" content="這是 og 描述導語"></head></html>'
    out = dp.apply_meta_fallback("短主文", html)
    assert out.startswith("這是 og 描述導語") and "短主文" in out
    # 已含則不重複補
    assert dp.apply_meta_fallback("這是 og 描述導語 已在內文", html) == "這是 og 描述導語 已在內文"

def test_is_listing_page():
    listing = BeautifulSoup("<body>" + "<article>一篇文章卡片內容</article>" * 6 + "</body>", "html.parser")
    assert dp.is_listing_page(listing) is True
    single = BeautifulSoup("<body><article><p>單篇文章正文</p></article></body>", "html.parser")
    assert dp.is_listing_page(single) is False

def test_remove_cmp_containers():
    soup = BeautifulSoup('<body><div id="onetrust-banner-sdk">cookie 同意</div><p>正文</p></body>', "html.parser")
    dp.remove_cmp_containers(soup)
    assert soup.find(id="onetrust-banner-sdk") is None
    assert "正文" in soup.get_text()

def test_quick_content_len():
    html = f'<script type="application/ld+json">{{"@type":"Article","articleBody":"{_BODY}"}}</script>'
    assert dp.quick_content_len(html) >= 200


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
    print(f"dom_parse：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
