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

# 中文停用詞（常見但無語意的詞）
_STOPWORDS = frozenset([
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都',
    '一', '上', '也', '很', '到', '說', '要', '去', '你', '會', '著',
    '沒有', '看', '好', '自己', '這', '那', '但', '為', '以', '使用',
    '可以', '如果', '所以', '因此', '還是', '已經', '只是', '可能',
    '直接', '感覺', '覺得', '知道', '時候', '方式', '來', '對', '被',
    '讓', '更', '最', '做', '用', '後', '得', '又', '還', '而', '把',
    '嗎', '啊', '喔', '哦', '吧', '呢', '哈', '嗯',
])

TOP_KEYWORDS = 25
TOP_PER_ARTICLE = 10
EMBEDDING_BATCH = 5     # Vertex AI 每批最多 5 筆
MAX_EMBED_CHARS = 1000  # 向量化時每篇取前 N 字（控制 token 成本）
MAX_CLUSTERS = 8


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
    texts = [
        f"{c.get('title', '')} {c.get('text') or c.get('content') or ''}"
        for c in contents
    ]

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
    """
    from google import genai

    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="us-central1",
    )

    all_embeddings: List[List[float]] = []
    for i in range(0, len(texts), EMBEDDING_BATCH):
        batch = texts[i: i + EMBEDDING_BATCH]
        resp = client.models.embed_content(
            model="text-multilingual-embedding-002",
            contents=batch,
        )
        for emb in resp.embeddings:
            all_embeddings.append(list(emb.values))

    return all_embeddings


def run_semantic_clustering(contents: List[Dict], project_id: str) -> Dict[str, Any]:
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

    embeddings = _get_embeddings(texts, project_id)
    if len(embeddings) < 2:
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
# Path 1 主函式
# ──────────────────────────────────────────────────────────────────────

def run(contents: List[Dict], project_id: str,
        log_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Path 1 主函式：TF-IDF + Vertex AI 語意分群。"""

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

    cluster_result: Dict[str, Any] = {"clusters": [], "n_clusters": 0}
    if project_id:
        _log("[Path 1b] Vertex AI 語意分群...")
        try:
            cluster_result = run_semantic_clustering(contents, project_id)
            _log(f"[Path 1b] 完成，共 {cluster_result['n_clusters']} 個主題群")
        except Exception as e:
            _log(f"[Path 1b] ⚠️ Vertex AI 失敗（略過分群）：{e}")
    else:
        _log("[Path 1b] 略過（未設定 GOOGLE_CLOUD_PROJECT）")

    # ⭐ 為每個主題群算「代表詞彙」：彙整該群文章的 per-article TF-IDF 關鍵字，取權重總和 Top N。
    _attach_cluster_keywords(cluster_result, tfidf.get("per_article", []))

    return {"tfidf": tfidf, "clusters": cluster_result}


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
