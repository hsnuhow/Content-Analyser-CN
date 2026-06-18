# -*- coding: utf-8 -*-
"""
Token 用量記帳 helper（待開發 10）。

把各家 LLM/embedding 回應的 usage 正規化成統一 {prompt, output, total}，
並提供彙整（依 category）與系統付用量落地（system_token_usage collection）。

分流原則（與使用者決策一致）：
- 用戶付（per-project LLM Key，走 LLMClient）→ 跟專案走（由 content-analyser 寫進 analyses/{aid}）。
- 系統付（系統 Vertex SA / GENAI：降噪、embedding、選擇器輔助）→ 進管理者後台（system_token_usage）。

所有抓取/寫入皆 best-effort：失敗只是該筆不記帳，絕不影響分析/爬取。
"""
from typing import Dict, List

SYSTEM_USAGE_COLLECTION = "system_token_usage"


def norm_usage(provider: str, raw) -> Dict[str, int]:
    """把各家 usage 物件正規化成 {prompt, output, total}。抓不到→全 0。"""
    try:
        p = (provider or "").lower()
        if p == "gemini":
            return {
                "prompt": int(getattr(raw, "prompt_token_count", 0) or 0),
                "output": int(getattr(raw, "candidates_token_count", 0) or 0),
                "total": int(getattr(raw, "total_token_count", 0) or 0),
            }
        if p == "claude":
            i = int(getattr(raw, "input_tokens", 0) or 0)
            o = int(getattr(raw, "output_tokens", 0) or 0)
            return {"prompt": i, "output": o, "total": i + o}
        if p == "openai":
            return {
                "prompt": int(getattr(raw, "prompt_tokens", 0) or 0),
                "output": int(getattr(raw, "completion_tokens", 0) or 0),
                "total": int(getattr(raw, "total_tokens", 0) or 0),
            }
    except Exception:
        pass
    return {"prompt": 0, "output": 0, "total": 0}


def aggregate(records: List[Dict]) -> Dict:
    """records: [{category, provider, model, prompt, output, total}] →
    {by_category:{cat:{prompt,output,total,calls}}, totals:{...}, provider, model, n_calls}。"""
    by_cat: Dict[str, Dict] = {}
    totals = {"prompt": 0, "output": 0, "total": 0}
    provider = model = ""
    for r in (records or []):
        cat = r.get("category", "other") or "other"
        d = by_cat.setdefault(cat, {"prompt": 0, "output": 0, "total": 0, "calls": 0})
        for k in ("prompt", "output", "total"):
            v = int(r.get(k, 0) or 0)
            d[k] += v
            totals[k] += v
        d["calls"] += 1
        provider = r.get("provider") or provider
        model = r.get("model") or model
    return {"by_category": by_cat, "totals": totals,
            "provider": provider, "model": model, "n_calls": len(records or [])}


def write_system_usage(db, records: List[Dict], context: Dict, embedding: Dict = None) -> None:
    """系統付用量 → system_token_usage（每 job 一筆 rollup，控制寫入量）。best-effort。
    context：{service, job_kind, job_id, project_id}。records 同 aggregate 輸入（token 型，如降噪）。
    embedding：{chars, n_texts}（字元計費的 embedding，估算，獨立記錄不混入 token 總額）。"""
    if db is None:
        return
    try:
        from firebase_admin import firestore
        summary = aggregate(records or [])
        emb = embedding or {}
        has_tokens = summary["totals"]["total"] > 0
        has_emb = bool(emb.get("chars"))
        if not has_tokens and not has_emb:
            return
        doc = {
            "payer": "system",
            "service": context.get("service", "analysis-pipeline"),
            "job_kind": context.get("job_kind", ""),
            "job_id": context.get("job_id", ""),
            "project_id": context.get("project_id", ""),
            "by_category": summary["by_category"],
            "prompt_tokens": summary["totals"]["prompt"],
            "output_tokens": summary["totals"]["output"],
            "total_tokens": summary["totals"]["total"],
            # embedding 以字元計費（非 token），獨立欄位、標示估算，不混入 *_tokens。
            "embedding": {"chars": int(emb.get("chars", 0) or 0),
                          "n_texts": int(emb.get("n_texts", 0) or 0),
                          "model": emb.get("model", ""),
                          "estimated": True} if has_emb else None,
            "at": firestore.SERVER_TIMESTAMP,
        }
        db.collection(SYSTEM_USAGE_COLLECTION).add(doc)
    except Exception as e:
        print(f"[TokenUsage] 系統用量寫入略過：{e}", flush=True)
