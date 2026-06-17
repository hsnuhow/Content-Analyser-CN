# -*- coding: utf-8 -*-
"""
知識庫索引與檢索（Phase 2，解耦式 RAG 的「系統檢索」端）。

- 索引：讀 kb_experts/{slug}/documents 的純文字 → 切塊 → 系統 SA Vertex embedding → kb_chunks。
- 檢索：以查詢（主報告摘要）向量化 → 在該專家 chunks 做記憶體 cosine top-K。

embedding 一律用系統 Service Account（Vertex，重用 nlp_path._get_embeddings）；
**生成端仍用用戶 Key**（在 audience_reports）。檢索失敗全面降級（回空），不擋生成。
"""
import numpy as np
from firebase_admin import firestore

from nlp_path import _get_embeddings, EMBED_MODEL, EMBED_DIM

CHUNKS_COLLECTION = "kb_chunks"
EXPERTS_COLLECTION = "kb_experts"

CHUNK_SIZE = 600        # 每塊約 N 字
CHUNK_OVERLAP = 80      # 塊間重疊字數
RETRIEVE_TOP_K = 5      # 檢索注入塊數
_MAX_DELETE = 1000


def chunk_text(text: str) -> list:
    """以段落為界、累積到 ~CHUNK_SIZE 字成一塊，塊間留 OVERLAP 重疊。"""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 <= CHUNK_SIZE:
            cur = (cur + "\n" + p) if cur else p
        else:
            if cur:
                chunks.append(cur)
            # 過長段落直接硬切
            if len(p) > CHUNK_SIZE:
                for i in range(0, len(p), CHUNK_SIZE - CHUNK_OVERLAP):
                    chunks.append(p[i:i + CHUNK_SIZE])
                cur = ""
            else:
                cur = (chunks[-1][-CHUNK_OVERLAP:] + "\n" + p) if chunks else p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if len(c.strip()) >= 20]


def _delete_expert_chunks(db, expert_slug: str) -> None:
    q = db.collection(CHUNKS_COLLECTION).where("expert_slug", "==", expert_slug).limit(_MAX_DELETE)
    while True:
        docs = list(q.stream())
        if not docs:
            break
        batch = db.batch()
        for d in docs:
            batch.delete(d.reference)
        batch.commit()
        if len(docs) < _MAX_DELETE:
            break


def reindex_expert(db, project_id: str, expert_slug: str) -> int:
    """重建某專家的 kb_chunks。回 chunk 數。"""
    docs = list(db.collection(EXPERTS_COLLECTION).document(expert_slug)
                .collection("documents").stream())
    # 先清舊塊（即使無文件也清，達成「刪光文件 → 無檢索」）
    _delete_expert_chunks(db, expert_slug)
    items = []  # (doc_id, seq, text)
    for d in docs:
        data = d.to_dict() or {}
        for seq, ch in enumerate(chunk_text(data.get("text", ""))):
            items.append((d.id, seq, ch))
    if not items:
        return 0
    vectors = _get_embeddings([t for _, _, t in items], project_id)
    if len(vectors) != len(items):
        raise RuntimeError(f"embedding 數({len(vectors)})≠chunk 數({len(items)})")
    batch = db.batch()
    n = 0
    for (doc_id, seq, text), vec in zip(items, vectors):
        ref = db.collection(CHUNKS_COLLECTION).document()
        batch.set(ref, {
            "expert_slug": expert_slug, "doc_id": doc_id, "seq": seq,
            "text": text, "vector": vec, "model": EMBED_MODEL, "dim": EMBED_DIM,
            "created_at": firestore.SERVER_TIMESTAMP,
        })
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()
    # 標記文件已索引
    for d in docs:
        try:
            d.reference.update({"indexed": True})
        except Exception:
            pass
    return n


def retrieve(db, project_id: str, expert_slug: str, query: str,
             top_k: int = RETRIEVE_TOP_K) -> list:
    """回該專家最相關的 top_k 塊文字。任何問題 → 回 []（降級純手冊）。"""
    try:
        rows = list(db.collection(CHUNKS_COLLECTION)
                    .where("expert_slug", "==", expert_slug).stream())
        if not rows:
            return []
        mats, texts = [], []
        for r in rows:
            d = r.to_dict() or {}
            v = d.get("vector")
            if v:
                mats.append(v)
                texts.append(d.get("text", ""))
        if not mats:
            return []
        qv = _get_embeddings([query[:1000]], project_id)
        if not qv:
            return []
        M = np.array(mats, dtype=float)
        q = np.array(qv[0], dtype=float)
        Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
        qn = q / (np.linalg.norm(q) + 1e-9)
        sims = Mn @ qn
        idx = np.argsort(sims)[::-1][:max(1, top_k)]
        return [texts[i] for i in idx]
    except Exception as e:
        print(f"[kb_index] 檢索失敗（降級純手冊）：{e}", flush=True)
        return []
