# -*- coding: utf-8 -*-
"""文字 / 來源處理層（自 nlp_path.py 抽出）。

純文字面的斷詞、來源分類、停用詞/過濾清單——只依賴 os/re/jieba（+ get_term_filters
內部 lazy 讀 Firestore，失敗自動退內建地板）。無 numpy/sklearn/vertexai 依賴，故可被
單獨 import 與測試（見 tests/test_text_processing.py）。

nlp_path 由此 import 這些名字使用；外部（app.py / pipeline / kb_index）不直接 import 本檔。
"""
import os
import re
from typing import List, Dict, Any

import jieba

# jieba 靜默模式
jieba.setLogLevel(20)

# ⭐ A 方案：改用 jieba 繁體辭典 dict.txt.big（含詞頻，專為繁中斷詞）為基底，
#    取代預設簡體導向辭典，大幅提升台灣繁中內容的斷詞品質。
_DICT_BIG = os.path.join(os.path.dirname(__file__), "dict.txt.big")
if os.path.exists(_DICT_BIG):
    try:
        jieba.set_dictionary(_DICT_BIG)
    except Exception as _e:
        print(f"[nlp] set_dictionary 失敗，沿用預設辭典：{_e}", flush=True)

# ⭐ 美妝/保養領域自訂詞典：辭典不懂這些複合詞，會切碎（如「維他命」→「他命」、
#    「傳明酸」「菸鹼醯胺」「初生光采」被拆開）。預先加入，提升斷詞與關鍵字品質。
_DOMAIN_TERMS = [
    # 成分
    "維他命", "維他命C", "維他命B3", "菸鹼醯胺", "傳明酸", "穀胱甘肽", "熊果素",
    "外泌體", "玻尿酸", "神經醯胺", "視黃醇", "A醇", "杜鵑花酸", "壬二酸", "水楊酸",
    "果酸", "杏仁酸", "甘醇酸", "乳酸", "胜肽", "角鯊烷", "積雪草", "蝦紅素",
    "維生素C", "維生素", "左旋C", "白藜蘆醇", "麴酸", "鞣花酸", "光甘草定",
    "AHA", "BHA", "PHA", "TXC", "B3", "VC",
    # 功效/概念
    "初生光采", "淨亮精萃", "淨亮精華油", "美白精華", "提亮", "透亮", "黑色素",
    "暗沉", "蠟黃", "膚色不均", "去黃", "淡斑", "煥膚", "刷酸", "妝前乳",
    "保養型底妝", "敏弱肌", "敏感肌", "光澤感", "白玉肌",
    # 品牌
    "香奈兒", "嬌蘭", "雅詩蘭黛", "資生堂", "蘭蔻", "契爾氏", "寶拉珍選",
]
for _t in _DOMAIN_TERMS:
    try:
        jieba.add_word(_t)
    except Exception:
        pass

# ⭐ moedict 蒸餾版補充詞庫（教育部辭典萃取的 2-4 字現代詞），檔存在才載入（由離線工具產出）。
_MOEDICT_USERDICT = os.path.join(os.path.dirname(__file__), "moedict_userdict.txt")
if os.path.exists(_MOEDICT_USERDICT):
    try:
        jieba.load_userdict(_MOEDICT_USERDICT)
    except Exception as _e:
        print(f"[nlp] moedict userdict 載入失敗（略過）：{_e}", flush=True)

# 媒體/出版品名碎片：標題常含媒體名，jieba 切出的片段（如「地球黃金線」→「黃金」）
# 會混進關鍵字與關聯規則。把全名當複合詞 add_word（整塊切出）再列入停用，
# 只移除品牌、保留「黃金」在「黃金比例」等一般用法。
_MEDIA_NAMES = [
    '地球黃金線', '黃金線', '車訊網', '發燒車訊', '國際車訊', '自由電子報',
    '科技新報', '風生活', '風傳媒', '車雲', '新聞雲', '汽車頻道',
]
for _m in _MEDIA_NAMES:
    try:
        jieba.add_word(_m)
    except Exception:
        pass

# 中文停用詞（常見但無語意的詞）
_STOPWORDS = frozenset([
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都',
    '一', '上', '也', '很', '到', '說', '要', '去', '你', '會', '著',
    '沒有', '看', '好', '自己', '這', '那', '但', '為', '以', '使用',
    '可以', '如果', '所以', '因此', '還是', '已經', '只是', '可能',
    '直接', '感覺', '覺得', '知道', '時候', '方式', '來', '對', '被',
    '讓', '更', '最', '做', '用', '後', '得', '又', '還', '而', '把',
    '嗎', '啊', '喔', '哦', '吧', '呢', '哈', '嗯',
    # 通用填充 / 公關稿動詞（內容無分析價值，污染 TF-IDF/分群/關聯）
    '我們', '你們', '他們', '它', '其', '提供', '推出', '表示', '指出',
    '目前', '透過', '擁有', '採用', '具備', '包括', '以及', '不僅',
    '甚至', '例如', '這款', '這個', '那個', '還有', '等等', '其中',
    '同時', '此外', '全新', '更加', '非常', '十分', '相當', '一款',
    # URL / 平台碎片（URL 被斷詞切碎的殘渣；雖已在 _text_for_keywords 清 URL，
    #   這裡作地板，連「在 ptt 上」這種裸詞提及也擋。dc=DC馬達/cc視情況保留語意者不列）。
    'http', 'https', 'www', 'com', 'net', 'org', 'html', 'htm', 'ptt', 'bbs', 'php',
    # 媒體名碎片（整塊複合詞，配合上方 add_word）
    *(_MEDIA_NAMES),
])

# 社群/論壇 UI 雜訊（回覆、留言等）：**只在社群/論壇來源**移除，媒體站不動
# （媒體文章的「回覆」可能是內容，如官方回覆）。依 URL 網域判定來源，因 dataset
# items 的 source_type 多半未填。
_SOCIAL_UI_STOPWORDS = (
    '回覆', '留言', '回文', '樓主', '小編', '轉發', '推文', '引用', '私訊',
    '回應', '回覆文', '原po', '原PO', '鄉民', '網友',
)
_SOCIAL_DOMAINS = (
    'facebook.com', 'fb.com', 'fb.watch', 'instagram.com', 'threads.net',
    'threads.com', 'dcard.tw', 'mobile01.com', 'ptt.cc', 'komica',
    'eyny.com', 'gamer.com.tw', 'bahamut',
)


def _is_social_url(url: str) -> bool:
    """URL 是否屬社群/論壇來源（Facebook/Instagram/Threads/Dcard/Mobile01/PTT/巴哈…）。"""
    u = (url or '').lower()
    return any(d in u for d in _SOCIAL_DOMAINS)


# ──────────────────────────────────────────────────────────────────────
# 來源分類 + 可後台編輯的字詞過濾清單（內建為地板，Firestore 額外項合併，60s 快取）
# 同一個詞在不同來源意義不同（「編輯」在媒體是內容、在論壇是功能按鈕文字），
# 故每個過濾詞帶 scope（在哪種來源才算垃圾）。套用發生在「還知道來源」的逐篇階段。
# ──────────────────────────────────────────────────────────────────────
SOURCE_TYPES = ('媒體', '社群', '論壇', '影音', '電商')


def _source_type(url: str) -> str:
    """依 URL 網域判定文本來源類型（預設『媒體』）。供 scope 過濾使用。"""
    u = (url or '').lower()
    if ('youtube.com' in u or 'youtu.be' in u or '/videos/' in u
            or '/reel' in u or 'tiktok.com' in u or 'bilibili' in u):
        return '影音'
    if (any(d in u for d in ('facebook.com', 'fb.com', 'fb.watch',
                             'instagram.com', 'threads.net', 'threads.com',
                             'twitter.com'))
            or '//x.com/' in u):   # x.com 需精確比對，避免誤中 winrex.com 等子字串
        return '社群'
    if any(d in u for d in ('ptt.cc', 'dcard.tw', 'mobile01.com',
                            'gamer.com.tw', 'bahamut', 'eyny.com', 'komica')):
        return '論壇'
    if any(d in u for d in ('shopee.', 'momoshop', 'momo.com.tw', 'pchome',
                            'books.com.tw', 'rakuten', 'amazon.', 'ruten')):
        return '電商'
    return '媒體'


# 內建地板：通用停用詞 = 全部來源；社群 UI 詞 = 社群+論壇。後台只能「增」不能刪這些。
_BUILTIN_ALL_SCOPE = frozenset(_STOPWORDS)
_BUILTIN_SOCIAL_SCOPE = frozenset(_SOCIAL_UI_STOPWORDS)

_TERM_FILTER_CACHE = {"val": None, "ts": 0.0}


def get_term_filters() -> Dict[str, Any]:
    """生效過濾清單 = 內建地板 + Firestore `system/config.term_filters`（後台可編輯）。

    Firestore 格式：term_filters: [{term, scope:[類型…], type?:"media"}]
      - scope 含 "全部" → 不分來源過濾；否則只在列出的來源類型過濾。
      - type=="media" → 也 jieba.add_word 整塊切出（媒體名）。
    回 {'all': set（全部來源停用）, 'by_source': {stype:set}, 'media_names': list}。
    60s 行程快取；讀失敗回退只用內建（與 crawler get_ad_blocklist 同模式）。
    """
    import time
    now = time.time()
    c = _TERM_FILTER_CACHE
    if c["val"] is not None and now - c["ts"] < 60:
        return c["val"]
    all_scope = set(_BUILTIN_ALL_SCOPE)
    by_source = {s: (set(_BUILTIN_SOCIAL_SCOPE) if s in ('社群', '論壇') else set())
                 for s in SOURCE_TYPES}
    media_names = list(_MEDIA_NAMES)
    try:
        from firebase_admin import firestore
        doc = firestore.client().collection("system").document("config").get()
        if doc.exists:
            for e in ((doc.to_dict() or {}).get("term_filters") or []):
                if not isinstance(e, dict):
                    continue
                term = str(e.get("term", "")).strip()
                if not term:
                    continue
                scopes = e.get("scope") or e.get("scopes") or ["全部"]
                if isinstance(scopes, str):
                    scopes = [scopes]
                if e.get("type") == "media":
                    media_names.append(term)
                if "全部" in scopes:
                    all_scope.add(term)
                else:
                    for s in scopes:
                        if s in by_source:
                            by_source[s].add(term)
    except Exception:
        pass
    # 媒體名：jieba 整塊切出 + 一律全部來源停用
    for m in media_names:
        try:
            jieba.add_word(m)
        except Exception:
            pass
        all_scope.add(m)
    val = {"all": all_scope, "by_source": by_source, "media_names": media_names}
    c["val"], c["ts"] = val, now
    return val


def _strip_terms(text: str, terms) -> str:
    """斷詞前以空白取代指定字詞（用於來源 scope 過濾）。"""
    for w in terms:
        if w in text:
            text = text.replace(w, ' ')
    return text


_URL_RE = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)


def _text_for_keywords(content: Dict) -> str:
    """組關鍵字/關聯用的文本：title + body；先清掉 URL，再依來源 scope 套用過濾詞。
    （URL 不清會被 jieba 切成 https/www/ptt/cc/bbs/html… 一堆垃圾 token 污染關鍵字。
    全部 scope 的詞於 _tokenize 統一過濾；此處處理 URL + 特定來源垃圾詞。
    embedding 用原始文本、不經此處理，故快取 key 不受影響。）"""
    t = f"{content.get('title', '')} {content.get('text') or content.get('content') or ''}"
    t = _URL_RE.sub(' ', t)   # 斷詞前清 URL（治本：URL 碎片不進關鍵字）
    scoped = get_term_filters()["by_source"].get(_source_type(content.get('url', '')))
    if scoped:
        t = _strip_terms(t, scoped)
    return t


def _tokenize(text: str) -> List[str]:
    """jieba 分詞，過濾停用詞（全部來源 scope，含後台增補）、純數字、單字、符號。"""
    stop_all = get_term_filters()["all"]
    tokens = jieba.cut(text, cut_all=False)
    result = []
    for t in tokens:
        t = t.strip()
        if (len(t) > 1
                and t not in stop_all
                and not re.fullmatch(r'[\d\s\W]+', t)):
            result.append(t)
    return result
