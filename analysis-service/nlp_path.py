# -*- coding: utf-8 -*-
"""
Path 1：數值分析層

1a. TF-IDF 關鍵字萃取（jieba + scikit-learn，本地執行，免費）
1b. Vertex AI text-multilingual-embedding-002 語意向量 + KMeans 分群
    （使用 Cloud Run Service Account / ADC，系統負擔，每次分析 < $0.01）

不需要本地 BERT 模型。Vertex AI API 取代傳統 Word2Vec / BERT。
"""
import re
import concurrent.futures
from typing import List, Dict, Any, Callable, Optional

import numpy as np
import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
from sklearn.preprocessing import normalize

# 文字 / 來源處理層已抽出至 text_processing.py（純 os/re/jieba，可單獨測）。
# 只 import nlp_path 實際用到的；其餘 _STOPWORDS 等若需要請直接 from text_processing import。
from text_processing import (
    _source_type, SOURCE_TYPES,
    get_term_filters, _text_for_keywords, _tokenize,
)

# 詞性白名單/填充判定（F2 建議用）。BRAND=專名/數字 → 保護；FILLER=副詞/語助等 → 建議。
# 不含 'eng'：英文 token 同時有品牌(Vornado/IRIS)與垃圾(http/https/ptt/cc)，一律保護會讓
# URL/平台垃圾永遠抓不到。改靠 Cloud NL salience 白名單保護真品牌（高 salience 實體），
# 讓非實體的英文垃圾能被建議出來。
_BRAND_POS = frozenset({'nr', 'ns', 'nt', 'nz', 'm', 'mq'})
_FILLER_POS = frozenset({'d', 'u', 'y', 'c', 'r', 'p', 'o', 'e', 'f'})


def _salient_entity_names(contents, max_docs: int = 15,
                          deadline_sec: float = 30.0,
                          min_salience: float = 0.01):
    """best-effort 取語料的 Cloud NL 實體名（領域詞/品牌）→ suggest_filters 白名單保護。

    解決「影音逐字稿把領域詞重複講」被誤判 chrome 的問題（如汽車稿的 鋁合金/結構/材質
    會是高 salience 實體 → 保護；然後/什麼 不是實體 → 仍可建議）。
    NL 未啟用 / 逾時 / 失敗 → 回空集合（演算法降級為純 jieba，不致命）。
    """
    names = set()
    try:
        from google.cloud import language_v1 as language
        client = language.LanguageServiceClient()
    except Exception:
        return names
    import time as _t
    from collections import defaultdict
    agg = defaultdict(float)
    deadline = _t.time() + deadline_sec
    for c in contents[:max_docs]:
        if _t.time() > deadline:
            break
        text = ((c.get("title", "") or "") + "\n"
                + (c.get("text") or c.get("content") or ""))[:8000].strip()
        if len(text) < 30:
            continue
        try:
            er = client.analyze_entities(request={
                "document": {"content": text,
                             "type_": language.Document.Type.PLAIN_TEXT},
                "encoding_type": language.EncodingType.UTF8})
        except Exception:
            return names  # API 未啟用等 → 直接降級（已收集者作廢，保守回空）
        for ent in er.entities:
            agg[ent.name] += ent.salience
    return {n for n, s in agg.items() if s >= min_salience}


def suggest_filters(contents: List[Dict], max_candidates: int = 60) -> Dict[str, Any]:
    """依爬蟲文本，用三信號找「該過濾但目前沒過濾」的候選垃圾詞，分來源建議 scope。

    信號：① 跨來源歧異度（某來源高、媒體低）② 同頁重複次數（chrome 會重複）
          ③ 詞性（副詞/語助→填充建議；專名/英文→品牌白名單保護）。
    排除：已在現行過濾清單（get_term_filters）者。輸出候選需人工勾選才生效。
    """
    import jieba.posseg as pseg
    from collections import Counter, defaultdict

    conf = get_term_filters()
    already = set(conf['all']) | set(conf['media_names'])
    for s in conf['by_source'].values():
        already |= s

    df = defaultdict(Counter)
    tfc = defaultdict(Counter)
    doc_n = Counter()
    for c in contents:
        text = f"{c.get('title', '')} {c.get('text') or c.get('content') or ''}"
        st = _source_type(c.get('url', ''))
        doc_n[st] += 1
        toks = [t.strip() for t in jieba.cut(text, cut_all=False)
                if len(t.strip()) > 1 and not re.fullmatch(r'[\d\s\W]+', t.strip())]
        for t in set(toks):
            df[st][t] += 1
        for t in toks:
            tfc[st][t] += 1

    def rate(s, t):
        return df[s][t] / doc_n[s] if doc_n[s] else 0.0

    def reps(s, t):
        return (tfc[s][t] / df[s][t]) if df[s][t] else 0.0

    nonmedia = [s for s in SOURCE_TYPES if s != '媒體']
    allt = set()
    for s in df:
        allt |= set(df[s])

    # Cloud NL salience 白名單：領域詞/品牌（影音逐字稿重複講的 鋁合金/結構 等）→ 保護。
    # best-effort，NL 未啟用/逾時 → 空集合，降級為純 jieba（不致命）。
    salient = _salient_entity_names(contents)

    poscache = {}

    def pos_of(t):
        if t not in poscache:
            try:
                poscache[t] = next(pseg.cut(t)).flag
            except StopIteration:
                poscache[t] = 'x'
        return poscache[t]

    cands = []
    for t in allt:
        if t in already:
            continue
        rm = rate('媒體', t)
        best = max(nonmedia, key=lambda s: rate(s, t))
        rb = rate(best, t)
        disc = rb - rm
        if rb < 0.66 or disc < 0.4:          # 信號①：跨來源歧異
            continue
        # 小樣本防呆：該來源至少 2 篇、且該詞在該來源出現於 ≥2 篇，
        # 否則單一頁的版面文字/領域詞會以 rate=1.0 灌爆候選（如單篇電商頁的購物車詞）。
        if doc_n[best] < 2 or df[best][t] < 2:
            continue
        p = pos_of(t)
        if p in _BRAND_POS:                    # 白名單：品牌/英文/專名/數字
            continue
        if t in salient:                        # 白名單：Cloud NL 實體（領域詞/品牌）
            continue
        rep = reps(best, t)
        if rep >= 3.0:                          # 信號②：同頁高重複 = 平台 chrome
            kind, score = 'chrome', disc + rep / 6.0
        elif p in _FILLER_POS:                  # 信號③：詞性=填充
            kind, score = '填充', disc + 0.3
        else:                                   # 名詞/低重複 → 需複查（防領域詞，預設不勾）
            kind, score = '需複查', disc
        cands.append({
            'term': t, 'scope': [best], 'kind': kind,
            'disc': round(disc, 2), 'rep': round(rep, 1), 'pos': p,
            'media_rate': round(rm, 2), 'score': round(score, 3),
        })
    cands.sort(key=lambda x: -x['score'])
    return {'candidates': cands[:max_candidates],
            'n_docs': int(sum(doc_n.values())),
            'by_source': {s: int(doc_n[s]) for s in doc_n},
            'n_protected_entities': len(salient)}


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

def _get_embeddings(texts: List[str], project_id: str, usage_sink: Dict = None) -> List[List[float]]:
    """呼叫 Vertex AI text-multilingual-embedding-002，取得語意向量。
    使用 Application Default Credentials（Cloud Run Service Account）。

    批次彼此獨立 → 並行送（EMBEDDING_WORKERS）並依批起始索引重組，確保 embeddings[i] ↔ texts[i] 對位。
    每次呼叫帶 HTTP 逾時（EMBED_CALL_TIMEOUT_MS），避免單一掛住的呼叫拖到數分鐘。
    usage_sink：token 記帳用（系統付）。給 dict 則累加實際送出的字元數與筆數（embedding 以字元計費，估算）。
    """
    from google import genai
    from google.genai import types
    import time as _time

    # 記帳（系統付，字元估算）：只算實際送進 Vertex 的 texts（cache miss 由上層已篩過）。
    if usage_sink is not None:
        try:
            usage_sink["chars"] = usage_sink.get("chars", 0) + sum(len(t or "") for t in texts)
            usage_sink["n_texts"] = usage_sink.get("n_texts", 0) + len(texts)
        except Exception:
            pass

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


def _embed_texts(texts: List[str], project_id: str, db=None, usage_sink: Dict = None) -> List[List[float]]:
    """帶 Firestore 快取的向量化：先查 embeddings/{key}，只對 miss 呼叫 Vertex，再寫回。
    db=None 或任何快取層錯誤 → 退回純 _get_embeddings（快取絕不擋分析）。對位以 texts 順序重組。
    usage_sink：傳遞給 _get_embeddings 記帳（只計 cache miss 的實際 API 字元）。"""
    if db is None:
        return _get_embeddings(texts, project_id, usage_sink)
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
            miss_vecs = _get_embeddings([key_to_text[k] for k in miss_keys], project_id, usage_sink)
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
                return _get_embeddings(texts, project_id, usage_sink)
        hits = len(uniq) - len(miss_keys)
        print(f"[nlp] embedding 快取：{hits}/{len(uniq)} 命中，{len(miss_keys)} 新算", flush=True)
        # 依原 texts 順序組回（重複文字共用同一向量）
        return [cached[k] for k in keys]
    except Exception as e:
        print(f"[nlp] embedding 快取層錯誤，退回純向量化：{e}", flush=True)
        return _get_embeddings(texts, project_id, usage_sink)


def run_semantic_clustering(contents: List[Dict], project_id: str, db=None,
                            usage_sink: Dict = None) -> Dict[str, Any]:
    """語意分群：Vertex AI Embedding → KMeans。

    回傳：
      clusters:   [{cluster_id, articles: [{url, title}]}]
      n_clusters: int
    usage_sink：embedding 記帳（系統付，字元估算）傳遞給 _embed_texts。
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

    embeddings = _embed_texts(texts, project_id, db, usage_sink)
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
            # 防禦性：x/y 為共現對端點，理應在 one 且計數 ≥ min_count（>0），
            # 故目前不會除零；但這是隱性不變式，明確 guard 一下，日後 vocab/籃子
            # 構造改動也不會讓關聯規則路徑整個 ZeroDivisionError 掛掉。
            denom_x, denom_y = one.get(x, 0), one.get(y, 0)
            if not denom_x or not denom_y:
                continue
            conf = c / denom_x
            lift = conf / (denom_y / n)
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
    from concurrent.futures import ThreadPoolExecutor
    # 取樣文本（每篇 ≥30 字才送）
    texts = []
    for c in contents[:NL_MAX_DOCS]:
        t = ((c.get("title", "") or "") + "\n"
             + (c.get("text") or c.get("content") or ""))[:8000].strip()
        if len(t) >= 30:
            texts.append(t)
    if not texts:
        return {"entities": [], "enabled": False, "reason": "無可分析文本"}

    def _one(text):
        """單篇：實體 + 情感。回 (entities_tuples, sentiment_or_None, error_or_None)。"""
        doc = {"content": text, "type_": language.Document.Type.PLAIN_TEXT}
        try:
            er = client.analyze_entities(
                request={"document": doc, "encoding_type": language.EncodingType.UTF8})
            ents = [(e.name, language.Entity.Type(e.type_).name, e.salience, len(e.mentions))
                    for e in er.entities]
        except Exception as e:
            return None, None, e
        score = None
        try:
            sr = client.analyze_sentiment(
                request={"document": doc, "encoding_type": language.EncodingType.UTF8})
            score = sr.document_sentiment.score
        except Exception:
            pass
        return ents, score, None

    # 平行處理（治本：序列 25 篇×2 呼叫易破 120s 上限導致 §3.2 整段被略過）。
    agg = defaultdict(lambda: {"salience": 0.0, "mentions": 0, "type": ""})
    sent_sum, sent_docs, used = 0.0, 0, 0
    last_err = None
    with ThreadPoolExecutor(max_workers=8) as ex:
        for ents, score, err in ex.map(_one, texts):
            if err is not None:
                last_err = err
                continue
            used += 1
            for name, typ, sal, men in ents:
                a = agg[name]
                a["salience"] += sal
                a["mentions"] += men
                a["type"] = typ
            if score is not None:
                sent_sum += score
                sent_docs += 1
    if used == 0:
        return {"entities": [], "enabled": False,
                "reason": (f"API 失敗（是否已啟用 language.googleapis.com？）：{last_err}"
                           if last_err else "無可分析文本")}
    # Cloud NL 獨立於 jieba 抽實體，媒體名（如「地球黃金線」）與停用詞雜訊會混進來 →
    # 比照關鍵字管道過濾：丟掉名稱屬媒體名/停用詞，或被媒體名包含（碎片，如「黃金」）的實體。
    _tf = get_term_filters()
    _stop_all, _media = _tf["all"], _tf["media_names"]

    def _is_entity_noise(name: str) -> bool:
        if name in _stop_all or name in _media:
            return True
        return any(name in m for m in _media if len(name) >= 2)
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
    emb_usage: Dict[str, int] = {"chars": 0, "n_texts": 0}  # embedding 記帳（系統付，字元估算）
    if project_id:
        _log("[Path 1b] Vertex AI 語意分群...")
        # best-effort + 硬時限：embedding 偶有數分鐘級延遲/SSL 重試，超過 CLUSTER_DEADLINE_SEC
        # 即降級單群，絕不拖垮 Path 1（TF-IDF 已成功，數值閘門不該被分群慢度卡死）。
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
                cluster_result = _ex.submit(
                    run_semantic_clustering, contents, project_id, db, emb_usage
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
            "assoc": assoc, "entities": entities,
            "embedding_usage": emb_usage}


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
