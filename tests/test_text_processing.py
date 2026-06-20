# -*- coding: utf-8 -*-
"""text_processing characterization 測試（analysis-service/text_processing.py）。

鎖住自 nlp_path 抽出的純文字/來源處理層行為，作為拆檔安全網。
需 jieba（已於正式 requirements；本地通常已裝）；get_term_filters 在無 Firestore 時退內建地板。

可直接執行：python3 tests/test_text_processing.py　｜　相容 pytest
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis-service"))

import text_processing as tp  # noqa: E402


# ── 來源分類 _source_type ──
def test_source_video():
    assert tp._source_type('https://www.youtube.com/watch?v=x') == '影音'
    assert tp._source_type('https://tiktok.com/@x') == '影音'

def test_source_social():
    assert tp._source_type('https://www.facebook.com/x') == '社群'
    assert tp._source_type('https://x.com/user/status/1') == '社群'

def test_source_forum():
    assert tp._source_type('https://www.ptt.cc/bbs/x.html') == '論壇'
    assert tp._source_type('https://www.dcard.tw/f/x') == '論壇'

def test_source_ecommerce():
    assert tp._source_type('https://shopee.tw/x') == '電商'
    assert tp._source_type('https://www.momoshop.com.tw/x') == '電商'

def test_source_media_default():
    assert tp._source_type('https://example.com/article') == '媒體'
    assert tp._source_type('') == '媒體'

def test_xcom_not_false_match():
    # x.com 精確比對，不應誤中 winrex.com
    assert tp._source_type('https://winrex.com/x') == '媒體'

def test_is_social_url():
    assert tp._is_social_url('https://ptt.cc/x') is True
    assert tp._is_social_url('https://example.com') is False


# ── 斷詞 _tokenize ──
def test_tokenize_filters_stopwords_and_singles():
    toks = tp._tokenize('這個 產品 真的 很 好用')
    assert '這個' not in toks and '很' not in toks   # 停用詞被濾（'真的' 不在清單、保留）
    assert all(len(t) > 1 for t in toks)             # 無單字（'扇' 等單字被丟）
    assert '產品' in toks and '好用' in toks

def test_tokenize_drops_pure_numbers_symbols():
    toks = tp._tokenize('循環扇 123 !!! 馬達')
    assert '123' not in toks and '!!!' not in toks
    assert '馬達' in toks


# ── 文本清理 _text_for_keywords（URL 不進關鍵字）──
def test_text_for_keywords_strips_url():
    out = tp._text_for_keywords({'title': '循環扇', 'text': '好用 https://www.ptt.cc/bbs/x.html 推薦', 'url': 'https://example.com/a'})
    assert 'http' not in out and 'ptt' not in out
    assert '循環扇' in out and '推薦' in out

def test_text_for_keywords_handles_missing():
    out = tp._text_for_keywords({})
    assert isinstance(out, str)


# ── 過濾清單 get_term_filters（無 Firestore 退內建地板）──
def test_get_term_filters_structure():
    f = tp.get_term_filters()
    assert set(f.keys()) == {'all', 'by_source', 'media_names'}
    assert '的' in f['all'] and 'http' in f['all']        # 內建停用詞地板
    assert set(f['by_source'].keys()) == set(tp.SOURCE_TYPES)
    # 社群/論壇 scope 含 UI 詞地板
    assert '回覆' in f['by_source']['論壇']


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
    print(f"text_processing：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
