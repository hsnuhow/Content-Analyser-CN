# -*- coding: utf-8 -*-
"""爬蟲後台設定載入（資料/邏輯分離）。

單一網站專屬的「垃圾關鍵字」等**資料**存 Firestore `crawler_config/*`，admin 可增/改/刪、
不必改碼部署。通用基礎（floor）仍在各純函式模組（text_clean 等）。與 get_ad_blocklist /
get_site_templates 同範式：floor 在程式、site-specific 在後台 + 60s 快取、讀失敗回退。
本模組只做 Firestore 載入（I/O），純函式模組維持純粹（以參數注入）。
"""
import time

_JUNK_CACHE = {"val": None, "ts": 0.0}


def get_extra_boilerplate():
    """單一媒體專屬的尾部樣板詞（中央社/自由/鏡週刊/TechNews 等的贊助·訂閱 CTA）。
    = Firestore `crawler_config/junk_keywords.boilerplate`（admin 可編）。60s 行程快取；讀失敗回空 list。
    通用樣板詞（版權/訂閱）的 floor 在 text_clean.TRAILING_BOILERPLATE。"""
    now = time.time()
    c = _JUNK_CACHE
    if c["val"] is not None and now - c["ts"] < 60:
        return c["val"]
    terms = []
    try:
        from firebase_admin import firestore
        doc = firestore.client().collection("crawler_config").document("junk_keywords").get()
        if doc.exists:
            v = (doc.to_dict() or {}).get("boilerplate")
            if isinstance(v, list):
                terms = [str(x).strip() for x in v if str(x).strip()]
    except Exception:
        terms = []
    c["val"], c["ts"] = terms, now
    return terms
