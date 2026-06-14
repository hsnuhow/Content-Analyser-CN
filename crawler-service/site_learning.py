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


def save_learned_selector(domain: str, selector: str,
                          sample_url: str = "", chars: int = 0,
                          cms: str = "") -> None:
    """把某網域學到的有效選擇器寫回 Firestore。no-op on failure。"""
    if not domain or not selector:
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
