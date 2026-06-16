# -*- coding: utf-8 -*-
"""
爬蟲研究器：持久化「學習到的選擇器」

當爬蟲對無模板的網域，透過 Gemini 找出有效主文選擇器後，把 domain→selector 寫回
Firestore（learned_selectors collection），下次（含重啟/其他實例）直接命中，不必再請
Gemini。等於爬蟲「自我修復、越爬越懂」，降低人工加模板的維護成本。

- load_learned_selectors()：讀全部已學選擇器（60s 快取）。
- save_learned_selector()：寫入單一網域的有效選擇器。
- detect_cms()：粗略判斷站台 CMS/架構類型（informational，供研究/除錯）。

Firestore 不可用時所有函式都優雅降級（回傳空 / no-op），不影響爬取。
"""
import time
from typing import Dict

_CACHE = {"data": {}, "ts": 0.0}
_COLLECTION = "learned_selectors"


def _client():
    from firebase_admin import firestore
    return firestore.client()


def _doc_id(domain: str) -> str:
    return domain.replace("/", "_").replace(".", "_")[:200]


def load_learned_selectors() -> Dict[str, str]:
    """回傳 {domain: selector}（60s 快取）。失敗回空 dict。"""
    now = time.time()
    if now - _CACHE["ts"] < 60:
        return _CACHE["data"]
    data: Dict[str, str] = {}
    try:
        for doc in _client().collection(_COLLECTION).stream():
            d = doc.to_dict() or {}
            dom = d.get("domain")
            sel = d.get("selector")
            if dom and sel:
                data[dom] = sel
    except Exception:
        data = _CACHE["data"]  # 沿用上次（避免暫時性錯誤清空）
    _CACHE["data"] = data
    _CACHE["ts"] = now
    return data


# 過寬的選擇器：命中過一次（≥門檻字數）就被持久化、之後對整個網域盲套，
# 會把雜訊也抽進來、長期污染該網域所有 URL 的抽取品質 → 拒絕寫入。
_TOO_BROAD_SELECTORS = {"body", "html", "main", "div", "*", "article", "section"}


def _is_valid_selector(selector: str) -> bool:
    s = (selector or "").strip().lower()
    if not s or len(s) > 200:
        return False
    # 純標籤名（無 . # [ > 空格 等限定）且落在過寬清單 → 拒絕
    if s in _TOO_BROAD_SELECTORS:
        return False
    return True


def save_learned_selector(domain: str, selector: str,
                          sample_url: str = "", chars: int = 0,
                          cms: str = "") -> None:
    """把某網域學到的有效選擇器寫回 Firestore。no-op on failure。"""
    if not domain or not selector:
        return
    if not _is_valid_selector(selector):
        print(f"[SiteLearning] 選擇器過寬/不合法，拒絕寫入 {domain}：{selector!r}", flush=True)
        return
    try:
        from firebase_admin import firestore
        _client().collection(_COLLECTION).document(_doc_id(domain)).set({
            "domain": domain,
            "selector": selector,
            "sample_url": sample_url,
            "chars": chars,
            "cms": cms,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        _CACHE["ts"] = 0.0  # 失效快取，下次重讀
        print(f"[SiteLearning] 學到 {domain} → {selector}（{chars} 字）", flush=True)
    except Exception as e:
        print(f"[SiteLearning] 儲存失敗（略過）：{e}", flush=True)


def detect_cms(html: str) -> str:
    """粗略 CMS/架構指紋（informational）。回傳類型字串。"""
    h = html[:200000]
    if "fullpage-content" in h or 'class="fullpage' in h:
        return "fullpage.js"
    if "listicle-body-content" in h or "article__body-content" in h:
        return "hearst"
    if '"articleBody"' in h and ("application/ld+json" in h):
        # JSON-LD 有正文（可能 Next.js styled-components，如 MirrorMedia）
        if "__next" in h or "self.__next_f" in h:
            return "nextjs-rsc+jsonld"
        return "jsonld"
    if "__next" in h or "self.__next_f" in h or '["$","p"' in h:
        return "nextjs-rsc"
    if "wp-content" in h or "wp-includes" in h:
        return "wordpress"
    return "generic"


# ──────────────────────────────────────────────────────────────────────
# 選擇器研究候選（research tool 產出 → admin 確認後升級為 learned_selectors）
# ──────────────────────────────────────────────────────────────────────
_CANDIDATES_COLLECTION = "selector_candidates"


def save_selector_candidate(domain: str, selectors: list, cms: str = "",
                            validated_chars: int = 0, sample_urls: list = None,
                            diagnosis: str = "") -> bool:
    """研究工具產出 → 寫入 selector_candidates/{domain}（待 admin 確認）。no-op on failure。

    per-domain 隔離：候選只進該網域自己的文件，錯誤無法擴散到別的網域。
    """
    if not domain:
        return False
    try:
        from firebase_admin import firestore
        _client().collection(_CANDIDATES_COLLECTION).document(_doc_id(domain)).set({
            "domain": domain,
            "selectors": list(selectors or []),
            "cms": cms,
            "validated_chars": int(validated_chars or 0),
            "sample_urls": list(sample_urls or []),
            "diagnosis": diagnosis,
            "status": "pending",
            "proposed_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        print(f"[Research] 候選已存：{domain} → {selectors}（{validated_chars} 字）", flush=True)
        return True
    except Exception as e:
        print(f"[Research] 候選儲存失敗（略過）：{e}", flush=True)
        return False


def list_selector_candidates(status: str = None) -> list:
    """列出候選（供 admin 確認頁）。status 可篩 'pending'/'approved'/'rejected'。"""
    try:
        col = _client().collection(_CANDIDATES_COLLECTION)
        docs = col.where("status", "==", status).stream() if status else col.stream()
        return [d.to_dict() | {"_id": d.id} for d in docs]
    except Exception as e:
        print(f"[Research] 列出候選失敗：{e}", flush=True)
        return []


def promote_candidate_to_learned(domain: str) -> bool:
    """admin 確認後：把候選的首選選擇器升級進 learned_selectors（主爬蟲執行時即讀取）。
    並把候選標記 approved。per-domain，只影響該網域。"""
    if not domain:
        return False
    try:
        from firebase_admin import firestore
        cref = _client().collection(_CANDIDATES_COLLECTION).document(_doc_id(domain))
        snap = cref.get()
        if not snap.exists:
            return False
        d = snap.to_dict() or {}
        sels = d.get("selectors") or []
        if not sels:
            return False
        save_learned_selector(domain, sels[0], chars=d.get("validated_chars", 0),
                              cms=d.get("cms", ""))
        cref.set({"status": "approved",
                  "approved_at": firestore.SERVER_TIMESTAMP}, merge=True)
        _CACHE["ts"] = 0.0  # 失效 learned_selectors 快取，下次重讀
        print(f"[Research] 候選升級為已學：{domain} → {sels[0]}", flush=True)
        return True
    except Exception as e:
        print(f"[Research] 候選升級失敗：{e}", flush=True)
        return False


def reject_candidate(domain: str) -> bool:
    """admin 拒絕候選。"""
    if not domain:
        return False
    try:
        from firebase_admin import firestore
        _client().collection(_CANDIDATES_COLLECTION).document(_doc_id(domain)).set(
            {"status": "rejected", "rejected_at": firestore.SERVER_TIMESTAMP}, merge=True)
        return True
    except Exception as e:
        print(f"[Research] 候選拒絕失敗：{e}", flush=True)
        return False
