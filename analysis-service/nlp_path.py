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
        f"{c.get('title', '')} {c.get('text', '')}"
        for c in contents
    ]

    vectorizer = TfidfVectorizer(
        tokenizer=_tokenize,
        max_features=500,
        min_df=1,
        max_df=0.95,
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
        f"{c.get('title', '')} {c.get('text', '')[:MAX_EMBED_CHARS]}"
        for c in contents
    ]

    embeddings = _get_embeddings(texts, project_id)
    X = normalize(np.array(embeddings))

    n_clusters = min(max(2, n // 3), MAX_CLUSTERS)
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

    return {"tfidf": tfidf, "clusters": cluster_result}
