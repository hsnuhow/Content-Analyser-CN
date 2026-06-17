# -*- coding: utf-8 -*-
"""
Path 1：數值分析層

1a. TF-IDF 關鍵字萃取（jieba + scikit-learn，本地執行，免費）
1b. Vertex AI text-multilingual-embedding-002 語意向量 + KMeans 分群
    （使用 Cloud Run Service Account / ADC，系統負擔，每次分析 < $0.01）

不需要本地 BERT 模型。Vertex AI API 取代傳統 Word2Vec / BERT。
"""
import os
import re
import concurrent.futures
from typing import List, Dict, Any, Callable, Optional

import numpy as np
import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
from sklearn.preprocessing import normalize

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


def _strip_social_ui(text: str) -> str:
    """移除社群 UI 雜訊詞（僅用於社群/論壇來源的文本，於斷詞前以空白取代）。"""
    for w in _SOCIAL_UI_STOPWORDS:
        if w in text:
            text = text.replace(w, ' ')
    return text


def _text_for_keywords(content: Dict) -> str:
    """組關鍵字/關聯用的文本：title + body；社群/論壇來源額外去 UI 雜訊。
    （embedding 用原始文本、不經此處理，故快取 key 不受影響。）"""
    t = f"{content.get('title', '')} {content.get('text') or content.get('content') or ''}"
    if _is_social_url(content.get('url', '')):
        t = _strip_social_ui(t)
    return t

TOP_KEYWORDS = 50
TOP_PER_ARTICLE = 10
EMBED_MODEL = "text-multilingual-embedding-002"  # 向量化模型（換模型時改這裡 + bump，快取 key 含此值會自動失效重算）
EMBED_DIM = 768                                  # 該模型預設維度（納入快取 key，避免跨模型/維度污染）
EMBED_CACHE_COLLECTION = "embeddings"            # Firestore 快取 collection：embeddings/{sha256(model:dim:text)}
EMBEDDING_BATCH = 20      # 每批筆數
EMBEDDING_RETRIES = 2     # 單批暫時性失敗（SSL EOF / 5xx / 逾時）重試次數
EMBEDDING_WORKERS = 4     # 並行批次數：批次彼此獨立，並行送可把總時間壓到 ≈ 最慢一批
EMBED_CALL_TIMEOUT_MS = 60000  # 單次 embed_content HTTP 逾時（毫秒）：避免單一呼叫無限掛住拖到數分鐘
MAX_EMBED_CHARS = 1000    # 向量化時每篇取前 N 字（控制 token 成本）
MAX_CLUSTERS = 8

# 慢且可降級的步驟各自的硬時限：超過即降級（單群／停用），絕不拖垮整個 Path 1。
# 數值閘門核心是 TF-IDF（秒級）+ 關聯規則（毫秒級），分群與 Cloud NL 都是 best-effort。
CLUSTER_DEADLINE_SEC = 240   # Vertex embedding + KMeans 全程上限
NL_DEADLINE_SEC = 120        # Cloud NL 實體 / 情感全程上限


def _tokenize(text: str) -> List[str]:
    """jieba 分詞，過濾停用詞、純數字、單字、符號。"""
    tokens = jieba.cut(text, cut_all=False)
    result = []
    for t in tokens:
        t = t.strip()
        if (len(t) > 1
                and t not in _STOPWORDS
                and not re.fullmatch(r'[\d\s\W]+', t)):
            result.append(t)
    return result


# ──────────────────────────────────────────────────────────────────────
# Path 1a：TF-IDF
# ──────────────────────────────────────────────────────────────────────

def run_tfidf(contents: List[Dict]) -> Dict[str, Any]:
    """TF-IDF 關鍵字萃取。

    回傳：
      top_keywords: [{keyword, weight}]  全體 Top 25
      per_article:  [{url, title, keywords: [{keyword, weight}]}]
    """
    texts = [_text_for_keywords(c) for c in contents]

    # 單篇時 max_df=0.95 會把所有詞（df=100%）過濾掉，改為 1.0 保留所有詞
    effective_max_df = 1.0 if len(texts) <= 1 else 0.95
    # ⭐ ngram_range=(1,2)：同時取單詞與雙詞（「初生光采」「美白精華」），雙詞語意更明確，
    #    對齊 REF 範本關鍵字品質。bigram 由 sklearn 在斷詞後以空白接合（如「初生 光采」）。
    vectorizer = TfidfVectorizer(
        tokenizer=_tokenize,
        ngram_range=(1, 2),
        max_features=500,
        min_df=1,
        max_df=effective_max_df,
        token_pattern=None,
    )
    matrix = vectorizer.fit_transform(texts)
    feature_names = vectorizer.get_feature_names_out()

    # 全體 Top Keywords（跨文章平均權重）
    mean_scores = np.asarray(matrix.mean(axis=0)).flatten()
    top_idx = mean_scores.argsort()[::-1][:TOP_KEYWORDS]
    top_keywords = [
        {"keyword": feature_names[i], "weight": round(float(mean_scores[i]), 4)}
        for i in top_idx
    ]

    # 逐篇 Top Keywords
    per_article = []
    for i, content in enumerate(contents):
        row = np.asarray(matrix[i].todense()).flatten()
        article_top = row.argsort()[::-1][:TOP_PER_ARTICLE]
        keywords = [
            {"keyword": feature_names[j], "weight": round(float(row[j]), 4)}
            for j in article_top
            if row[j] > 0
        ]
        per_article.append({
            "url": content.get("url", ""),
            "title": content.get("title", ""),
            "keywords": keywords,
        })

    return {"top_keywords": top_keywords, "per_article": per_article}


# ──────────────────────────────────────────────────────────────────────
# Path 1b：Vertex AI Embedding + 語意分群
# ──────────────────────────────────────────────────────────────────────

def _get_embeddings(texts: List[str], project_id: str) -> List[List[float]]:
    """呼叫 Vertex AI text-multilingual-embedding-002，取得語意向量。
    使用 Application Default Credentials（Cloud Run Service Account）。

    批次彼此獨立 → 並行送（EMBEDDING_WORKERS）並依批起始索引重組，確保 embeddings[i] ↔ texts[i] 對位。
    每次呼叫帶 HTTP 逾時（EMBED_CALL_TIMEOUT_MS），避免單一掛住的呼叫拖到數分鐘。
    """
    from google import genai
    from google.genai import types
    import time as _time

    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="us-central1",
        http_options=types.HttpOptions(timeout=EMBED_CALL_TIMEOUT_MS),
    )

    batch_starts = list(range(0, len(texts), EMBEDDING_BATCH))

    def _embed_batch(start: int) -> List[List[float]]:
        batch = texts[start: start + EMBEDDING_BATCH]
        last = None
        for attempt in range(EMBEDDING_RETRIES):
            try:
                resp = client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=batch,
                )
                return [list(emb.values) for emb in resp.embeddings]
            except Exception as e:
                last = e
                if attempt < EMBEDDING_RETRIES - 1:
                    print(f"[nlp] embedding 批 @{start} 暫時性失敗，"
                          f"{1.5*(attempt+1):.1f}s 後重試：{e}", flush=True)
                    _time.sleep(1.5 * (attempt + 1))
        raise last  # 重試後仍失敗 → 上拋讓 run_semantic_clustering 降級

    results: Dict[int, List[List[float]]] = {}
    workers = min(EMBEDDING_WORKERS, len(batch_starts)) or 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_embed_batch, s): s for s in batch_starts}
        for fut in concurrent.futures.as_completed(futs):
            results[futs[fut]] = fut.result()  # 任一批拋出 → 整體上拋降級

    all_embeddings: List[List[float]] = []
    for s in batch_starts:
        all_embeddings.extend(results[s])
    return all_embeddings


def _emb_key(text: str) -> str:
    """快取 key：sha256(model:dim:text)。含 model+dim → 換模型/維度自動失效，不會誤用舊向量。"""
    import hashlib
    return hashlib.sha256(
        f"{EMBED_MODEL}:{EMBED_DIM}:{text}".encode("utf-8")).hexdigest()


def _embed_texts(texts: List[str], project_id: str, db=None) -> List[List[float]]:
    """帶 Firestore 快取的向量化：先查 embeddings/{key}，只對 miss 呼叫 Vertex，再寫回。
    db=None 或任何快取層錯誤 → 退回純 _get_embeddings（快取絕不擋分析）。對位以 texts 順序重組。"""
    if db is None:
        return _get_embeddings(texts, project_id)
    try:
        keys = [_emb_key(t) for t in texts]
        uniq = list(dict.fromkeys(keys))  # 去重（同文多篇只查/算一次）
        col = db.collection(EMBED_CACHE_COLLECTION)
        cached: Dict[str, List[float]] = {}
        refs = [col.document(k) for k in uniq]
        for snap in db.get_all(refs):
            if snap.exists:
                v = (snap.to_dict() or {}).get("vector")
                if v:
                    cached[snap.id] = list(v)
        miss_keys = [k for k in uniq if k not in cached]
        # miss 的代表文字（每個 uniq key 對到首次出現的 text）
        key_to_text = {}
        for t, k in zip(texts, keys):
            key_to_text.setdefault(k, t)
        if miss_keys:
            miss_vecs = _get_embeddings([key_to_text[k] for k in miss_keys], project_id)
            if len(miss_vecs) == len(miss_keys):
                from firebase_admin import firestore as _fs
                batch = db.batch()
                for k, vec in zip(miss_keys, miss_vecs):
                    cached[k] = vec
                    batch.set(col.document(k), {
                        "vector": vec, "model": EMBED_MODEL, "dim": EMBED_DIM,
                        "created_at": _fs.SERVER_TIMESTAMP,
                    })
                try:
                    batch.commit()
                except Exception as e:
                    print(f"[nlp] embedding 快取寫回失敗（不影響分析）：{e}", flush=True)
            else:
                # 數量不符 → 不信快取，整批重算（安全）
                return _get_embeddings(texts, project_id)
        hits = len(uniq) - len(miss_keys)
        print(f"[nlp] embedding 快取：{hits}/{len(uniq)} 命中，{len(miss_keys)} 新算", flush=True)
        # 依原 texts 順序組回（重複文字共用同一向量）
        return [cached[k] for k in keys]
    except Exception as e:
        print(f"[nlp] embedding 快取層錯誤，退回純向量化：{e}", flush=True)
        return _get_embeddings(texts, project_id)


def run_semantic_clustering(contents: List[Dict], project_id: str, db=None) -> Dict[str, Any]:
    """語意分群：Vertex AI Embedding → KMeans。

    回傳：
      clusters:   [{cluster_id, articles: [{url, title}]}]
      n_clusters: int
    """
    n = len(contents)
    if n < 3:
        return {
            "clusters": [{
                "cluster_id": 0,
                "articles": [
                    {"url": c.get("url", ""), "title": c.get("title", "")}
                    for c in contents
                ],
            }],
            "n_clusters": 1,
        }

    # 取前 MAX_EMBED_CHARS 字做向量（控制 API 費用）
    texts = [
        f"{c.get('title', '')} {(c.get('text') or c.get('content') or '')[:MAX_EMBED_CHARS]}"
        for c in contents
    ]

    embeddings = _embed_texts(texts, project_id, db)
    # 對位防呆：分群用 labels[i] ↔ contents[i] 對應。若 embeddings 數量與 texts 不符
    # （某批少回 → 錯位），後續會把文章分到錯群 → 寧可降級單群，不冒錯位風險。
    if len(embeddings) < 2 or len(embeddings) != len(texts):
        if len(embeddings) != len(texts):
            print(f"[nlp] embeddings 數({len(embeddings)})≠texts 數({len(texts)})，降級單群避免錯位",
                  flush=True)
        return {
            "clusters": [{"cluster_id": 0, "articles": [
                {"url": c.get("url", ""), "title": c.get("title", "")} for c in contents
            ]}],
            "n_clusters": 1,
        }
    X = normalize(np.array(embeddings))

    n_clusters = min(max(2, n // 3), MAX_CLUSTERS, len(embeddings))
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(X)

    clusters = []
    for cid in range(n_clusters):
        indices = [i for i, lbl in enumerate(labels) if lbl == cid]
        clusters.append({
            "cluster_id": cid,
            "articles": [
                {"url": contents[i].get("url", ""), "title": contents[i].get("title", "")}
                for i in indices
            ],
        })

    return {"clusters": clusters, "n_clusters": n_clusters}


# ──────────────────────────────────────────────────────────────────────
# Path 1c：關聯規則挖掘（FP 風格的頻繁組合 + 規則；純本地、快）
# ──────────────────────────────────────────────────────────────────────
ASSOC_VOCAB = 30          # 以語料級 Top N 關鍵字為「品項詞彙」（含主題核心詞，非各篇獨特詞）
ASSOC_MIN_SUPPORT = 0.10  # 頻繁組合最低支持度（出現於 ≥10% 篇）
ASSOC_MIN_CONF = 0.5      # 關聯規則最低信賴度
ASSOC_MAX_RULES = 15


def _article_terms(text: str) -> set:
    """文章詞集合（unigram + bigram），與 TfidfVectorizer(ngram(1,2)) 同口徑：
    _tokenize 出 unigram，相鄰兩詞以空白接合成 bigram，供與語料級關鍵字詞彙比對。"""
    toks = _tokenize(text)
    terms = set(toks)
    for i in range(len(toks) - 1):
        terms.add(toks[i] + " " + toks[i + 1])
    return terms


def run_association(tfidf: Dict, contents: List[Dict]) -> Dict[str, Any]:
    """關聯探勘：以「語料級 Top 關鍵字」為品項詞彙，各篇的籃子＝它實際含有的那些詞，
    找頻繁共現組合 + 關聯規則。回 {itemsets, rules}。
    為何不用每篇 TF-IDF top 詞：那取的是「獨特詞」，主題核心詞（如品牌/車系，IDF 低）反被排除，
    導致跨篇湊不出重複組合。改用語料級詞彙 → 浮出「Taycan＋電動」這類真正有意義的主題共現。
    方法論一＝高 support 有效組合；方法論二＝高 lift（強關聯）切角。純本地、毫秒級。"""
    from itertools import combinations
    from collections import Counter
    vocab = {k["keyword"] for k in (tfidf.get("top_keywords") or [])[:ASSOC_VOCAB]
             if k.get("keyword")}
    if not vocab:
        return {"itemsets": [], "rules": []}
    txns = []
    for c in contents:
        items = _article_terms(_text_for_keywords(c)) & vocab
        if items:
            txns.append(items)
    n = len(txns)
    if n < 4:
        return {"itemsets": [], "rules": []}
    one, pair = Counter(), Counter()
    for t in txns:
        for it in t:
            one[it] += 1
        for a, b in combinations(sorted(t), 2):
            pair[(a, b)] += 1
    min_count = max(3, int(ASSOC_MIN_SUPPORT * n))
    freq_pairs = sorted([(p, c) for p, c in pair.items() if c >= min_count],
                        key=lambda x: -x[1])
    itemsets = [{"items": list(p), "support": round(c / n, 3), "count": c}
                for p, c in freq_pairs[:20]]
    rules = []
    for (a, b), c in freq_pairs:
        for x, y in ((a, b), (b, a)):
            conf = c / one[x]
            lift = conf / (one[y] / n)
            if conf >= ASSOC_MIN_CONF and lift > 1.0:
                rules.append({"antecedent": x, "consequent": y,
                              "support": round(c / n, 3), "confidence": round(conf, 3),
                              "lift": round(lift, 2), "count": c})
    rules.sort(key=lambda r: (-r["lift"], -r["confidence"]))
    return {"itemsets": itemsets, "rules": rules[:ASSOC_MAX_RULES]}


# ──────────────────────────────────────────────────────────────────────
# Path 1d：Cloud Natural Language 實體 + 情感（需 language.googleapis.com；優雅降級）
# ──────────────────────────────────────────────────────────────────────
NL_MAX_DOCS = 25  # 取樣篇數：每篇 2 次 API 呼叫（實體+情感），控制在 NL_DEADLINE_SEC 內完成


def run_entities_sentiment(contents: List[Dict]) -> Dict[str, Any]:
    """Cloud NL：實體抽取（salience）+ 整篇情感。中文用 analyze_entities + analyze_sentiment
    （entity-sentiment 對中文支援有限，故分開）。未啟用 API / 未安裝套件 → 回 enabled=False 降級。
    回 {entities:[{name,type,salience,mentions}], avg_sentiment, n_docs, enabled, reason?}。"""
    try:
        from google.cloud import language_v1 as language
    except Exception:
        return {"entities": [], "enabled": False, "reason": "google-cloud-language 未安裝"}
    try:
        client = language.LanguageServiceClient()
    except Exception as e:
        return {"entities": [], "enabled": False, "reason": str(e)}
    from collections import defaultdict
    agg = defaultdict(lambda: {"salience": 0.0, "mentions": 0, "type": ""})
    sent_sum, sent_docs, used = 0.0, 0, 0
    for c in contents[:NL_MAX_DOCS]:
        text = ((c.get("title", "") or "") + "\n"
                + (c.get("text") or c.get("content") or ""))[:8000].strip()
        if len(text) < 30:
            continue
        doc = {"content": text, "type_": language.Document.Type.PLAIN_TEXT}
        try:
            er = client.analyze_entities(
                request={"document": doc, "encoding_type": language.EncodingType.UTF8})
        except Exception as e:
            return {"entities": [], "enabled": False,
                    "reason": f"API 失敗（是否已啟用 language.googleapis.com？）：{e}"}
        used += 1
        for ent in er.entities:
            a = agg[ent.name]
            a["salience"] += ent.salience
            a["mentions"] += len(ent.mentions)
            a["type"] = language.Entity.Type(ent.type_).name
        try:
            sr = client.analyze_sentiment(
                request={"document": doc, "encoding_type": language.EncodingType.UTF8})
            sent_sum += sr.document_sentiment.score
            sent_docs += 1
        except Exception:
            pass
    if used == 0:
        return {"entities": [], "enabled": False, "reason": "無可分析文本"}
    # Cloud NL 獨立於 jieba 抽實體，媒體名（如「地球黃金線」）與停用詞雜訊會混進來 →
    # 比照關鍵字管道過濾：丟掉名稱屬媒體名/停用詞，或被媒體名包含（碎片，如「黃金」）的實體。
    def _is_entity_noise(name: str) -> bool:
        if name in _STOPWORDS or name in _MEDIA_NAMES:
            return True
        return any(name in m for m in _MEDIA_NAMES if len(name) >= 2)
    ents = [{"name": k, "type": v["type"], "salience": round(v["salience"], 4),
             "mentions": v["mentions"]} for k, v in agg.items()
            if not _is_entity_noise(k)]
    ents.sort(key=lambda e: -e["salience"])
    return {"entities": ents[:25], "enabled": True, "n_docs": used,
            "avg_sentiment": round(sent_sum / sent_docs, 3) if sent_docs else None}


# ──────────────────────────────────────────────────────────────────────
# Path 1 主函式
# ──────────────────────────────────────────────────────────────────────

def run(contents: List[Dict], project_id: str,
        log_fn: Optional[Callable] = None, db=None) -> Dict[str, Any]:
    """Path 1 主函式：TF-IDF + Vertex AI 語意分群。db 供 embedding 快取（None 則不快取）。"""

    def _log(msg: str):
        print(msg, flush=True)
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    _log(f"[Path 1a] TF-IDF 分析（{len(contents)} 篇）...")
    tfidf = run_tfidf(contents)
    top5 = [k["keyword"] for k in tfidf["top_keywords"][:5]]
    _log(f"[Path 1a] 完成。Top 5：{top5}")

    def _degraded_clusters() -> Dict[str, Any]:
        """分群降級：全部歸一群（報告仍可用，只是少了語意切分）。"""
        return {"clusters": [{"cluster_id": 0, "articles": [
            {"url": c.get("url", ""), "title": c.get("title", "")} for c in contents
        ]}], "n_clusters": 1 if contents else 0}

    cluster_result: Dict[str, Any] = {"clusters": [], "n_clusters": 0}
    if project_id:
        _log("[Path 1b] Vertex AI 語意分群...")
        # best-effort + 硬時限：embedding 偶有數分鐘級延遲/SSL 重試，超過 CLUSTER_DEADLINE_SEC
        # 即降級單群，絕不拖垮 Path 1（TF-IDF 已成功，數值閘門不該被分群慢度卡死）。
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
                cluster_result = _ex.submit(
                    run_semantic_clustering, contents, project_id, db
                ).result(timeout=CLUSTER_DEADLINE_SEC)
            _log(f"[Path 1b] 完成，共 {cluster_result['n_clusters']} 個主題群")
        except concurrent.futures.TimeoutError:
            _log(f"[Path 1b] ⚠️ 分群超過 {CLUSTER_DEADLINE_SEC}s，降級單群繼續")
            cluster_result = _degraded_clusters()
        except Exception as e:
            _log(f"[Path 1b] ⚠️ Vertex AI 失敗（降級單群）：{e}")
            cluster_result = _degraded_clusters()
    else:
        _log("[Path 1b] 略過（未設定 GOOGLE_CLOUD_PROJECT）")

    # ⭐ 為每個主題群算「代表詞彙」：彙整該群文章的 per-article TF-IDF 關鍵字，取權重總和 Top N。
    _attach_cluster_keywords(cluster_result, tfidf.get("per_article", []))

    # Path 1c：關聯探勘（純本地、毫秒級，不會失敗就降級空集）
    _log("[Path 1c] 關聯規則挖掘（頻繁共現 + 規則）...")
    try:
        assoc = run_association(tfidf, contents)
        _log(f"[Path 1c] 完成，共 {len(assoc.get('rules', []))} 條規則／"
             f"{len(assoc.get('itemsets', []))} 組頻繁組合")
    except Exception as e:
        _log(f"[Path 1c] ⚠️ 關聯探勘失敗（略過）：{e}")
        assoc = {"itemsets": [], "rules": []}

    # Path 1d：Cloud NL 實體 + 情感（best-effort + 硬時限；未啟用 API / 逾時皆優雅降級）
    _log("[Path 1d] Cloud Natural Language 實體／情感分析...")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
            entities = _ex.submit(run_entities_sentiment, contents).result(
                timeout=NL_DEADLINE_SEC)
        if entities.get("enabled"):
            _log(f"[Path 1d] 完成，{entities.get('n_docs')} 篇／"
                 f"{len(entities.get('entities', []))} 個實體")
        else:
            _log(f"[Path 1d] 降級略過：{entities.get('reason', '未知')}")
    except concurrent.futures.TimeoutError:
        _log(f"[Path 1d] ⚠️ 超過 {NL_DEADLINE_SEC}s，降級略過")
        entities = {"entities": [], "enabled": False, "reason": f"逾時 >{NL_DEADLINE_SEC}s"}
    except Exception as e:
        _log(f"[Path 1d] ⚠️ 失敗（略過）：{e}")
        entities = {"entities": [], "enabled": False, "reason": str(e)}

    return {"tfidf": tfidf, "clusters": cluster_result,
            "assoc": assoc, "entities": entities}


def _attach_cluster_keywords(cluster_result: Dict, per_article: List[Dict],
                             top_n: int = 8) -> None:
    """為每個 cluster 加上 `keywords`（代表詞彙）：彙整群內文章的 TF-IDF 關鍵字權重。"""
    kw_by_url = {a.get("url", ""): a.get("keywords", []) for a in per_article}
    kw_by_title = {a.get("title", ""): a.get("keywords", []) for a in per_article}
    for g in cluster_result.get("clusters", []):
        agg: Dict[str, float] = {}
        for art in g.get("articles", []):
            kws = kw_by_url.get(art.get("url", "")) or kw_by_title.get(art.get("title", "")) or []
            for k in kws:
                agg[k["keyword"]] = agg.get(k["keyword"], 0.0) + float(k.get("weight", 0))
        top = sorted(agg.items(), key=lambda x: x[1], reverse=True)[:top_n]
        g["keywords"] = [kw for kw, _ in top]
