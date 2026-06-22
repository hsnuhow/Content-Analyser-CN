# -*- coding: utf-8 -*-
"""爬蟲後台設定載入（資料/邏輯分離）。

單一網站專屬的「垃圾關鍵字」等**資料**存 Firestore `crawler_config/*`，admin 可增/改/刪、
不必改碼部署。通用基礎（floor）仍在各純函式模組（text_clean 等）。與 get_ad_blocklist /
get_site_templates 同範式：floor 在程式、site-specific 在後台 + 60s 快取、讀失敗回退。
本模組只做 Firestore 載入（I/O），純函式模組維持純粹（以參數注入）。
"""
import time

_JUNK_CACHE = {"val": None, "ts": 0.0}
_PAYWALL_CACHE = {"val": None, "ts": 0.0}

# 付費牆偵測 floor（內建安全基線；admin 可於 Firestore crawler_config/paywall 增補）。
# A 型「明確 CTA 標記」——內容裡出現即判定不完整（實測天下；工商/UDN/鏡週刊多屬此類）。
_PAYWALL_MARKERS_FLOOR = (
    "訂戶限定", "解鎖訂戶限定", "查看訂閱方案", "不限篇數暢讀", "立即購買",
    "訂閱看全文", "會員專屬", "付費訂閱", "登入後繼續閱讀", "升級會員",
    "訂閱解鎖", "加入會員看全文", "本文為訂閱", "付費會員", "訂閱會員專屬",
    "本篇為訂閱限定", "訂閱以閱讀全文",
)
# B 型「靜默截斷付費牆網域」——付費牆是 JS 遮罩、抓不到 CTA 文字，只抓到引言（實測商周/端傳媒）。
# 該網域抽到的內容短於門檻（字）即視為被付費牆截斷的不完整內容。
_PAYWALL_DOMAINS_FLOOR = {
    "businessweekly.com.tw": 700,
    "theinitium.com": 700,
    "ctee.com.tw": 700,
}


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


def get_paywall_config():
    """付費牆/不完整偵測設定：內建 floor + Firestore `crawler_config/paywall`（admin 可編）+ 60s 快取。
    回 {"markers": (...CTA 標記...), "domains": {網域: 最小完整長度門檻}}。讀失敗回退只用 floor。
    供 page_classify.detect_paywall_incomplete 注入（純函式不碰 Firestore）。"""
    now = time.time()
    c = _PAYWALL_CACHE
    if c["val"] is not None and now - c["ts"] < 60:
        return c["val"]
    markers = list(_PAYWALL_MARKERS_FLOOR)
    domains = dict(_PAYWALL_DOMAINS_FLOOR)
    try:
        from firebase_admin import firestore
        doc = firestore.client().collection("crawler_config").document("paywall").get()
        if doc.exists:
            d = doc.to_dict() or {}
            for m in (d.get("markers") or []):
                if isinstance(m, str) and m.strip() and m.strip() not in markers:
                    markers.append(m.strip())
            extra = d.get("domains")
            if isinstance(extra, dict):
                for dom, ml in extra.items():
                    try:
                        domains[str(dom)] = int(ml)
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass
    val = {"markers": tuple(markers), "domains": domains}
    c["val"], c["ts"] = val, now
    return val
