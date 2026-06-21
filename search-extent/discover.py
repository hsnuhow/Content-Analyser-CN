# -*- coding: utf-8 -*-
"""
search-extent 子功能 B：供給側·內容發現

關鍵字 → Vertex Gemini + Google Search grounding → 推薦爬取 URL 清單。
grounding 在 Google 伺服器端執行（非本服務直爬 Google），故無資料中心 IP / CAPTCHA 問題；
用 Cloud Run Service Account 的 ADC，不需 API key、不需建 CSE。

輸出純情報（URL + metadata），不爬正文、不分析、不持久化。
"""
import os
import json
import ipaddress
import socket
import urllib.request
import threading
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

import requests

# ── 來源類型分類（與 analysis-pipeline _source_type 對齊；此處獨立一份，服務不共用程式碼）──
_VIDEO = ('youtube.com', 'youtu.be', '/videos/', '/reel', 'tiktok.com', 'bilibili')
_SOCIAL = ('facebook.com', 'fb.com', 'fb.watch', 'instagram.com', 'threads.net', 'threads.com')
_FORUM = ('ptt.cc', 'pttweb.cc', 'dcard.tw', 'mobile01.com', 'gamer.com.tw', 'bahamut', 'eyny.com', 'komica')
_ECOM = ('shopee.', 'momoshop', 'momo.com.tw', 'pchome', 'books.com.tw', 'rakuten', 'amazon.',
         'ruten', 'coupang', 'buy.yahoo', 'trplus')
_HK = ('.hk', 'hongkong', 'hk01', 'stheadline')
_TW = ('.tw', 'my-best.com', 'pixnet', 'vocus')


def _source_type(u: str) -> str:
    s = (u or '').lower()
    if any(d in s for d in _VIDEO):
        return '影音'
    if any(d in s for d in _SOCIAL):
        return '社群'
    if any(d in s for d in _FORUM):
        return '論壇'
    if any(d in s for d in _ECOM):
        return '電商'
    return '媒體'


def _region(u: str) -> str:
    d = (urlparse(u).hostname or '').lower()
    if any(h in d for h in _HK):
        return 'HK'
    if any(h in d for h in _TW) or any(x in (u or '').lower() for x in ('/tw/', 'tw.')):
        return 'TW'
    return '?'


_LISTING_HINTS = ('/search', '/list', '/category', '/categories', '/tag/', '/tags/',
                  '/promotion', 's=', 'q=', 'query=')


def _flag(u: str) -> str:
    """非文章頁旗標：首頁 / 列表/搜尋/分類頁（爬蟲多半會 skip，預設不勾）。"""
    p = urlparse(u)
    path = (p.path or '/').rstrip('/')
    if not path:
        return '首頁'
    low = u.lower()
    if any(h in low for h in _LISTING_HINTS):
        return '列表頁'
    return ''


_creds_cache = None
_creds_lock = threading.Lock()


def _access_token():
    """Cloud Run SA 的 ADC access token（cloud-platform scope）。

    module-level 快取 credential 物件；只有在無快取或 token 失效（not creds.valid）
    時才 refresh，避免每次請求都打 metadata/token endpoint（吃 Cloudflare 預算）。
    grounding 多 thread 平行呼叫，故用 lock 保護快取與 refresh。
    """
    global _creds_cache
    import google.auth
    import google.auth.transport.requests
    with _creds_lock:
        creds = _creds_cache
        if creds is None:
            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"])
            _creds_cache = creds
        if not creds.valid:
            creds.refresh(google.auth.transport.requests.Request())
        return creds.token


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))


_DEFAULT_ANGLES = [
    "台灣 {q} 選購指南 評測 比較（繁體中文、台灣網站）",
    "台灣 {q} 推薦 開箱 心得 ptt dcard mobile01",
    "台灣 {q} youtube 開箱 影音",
]
_MODEL = "gemini-2.5-flash"


def _ground(project, prompt):
    """單次 grounding 呼叫，回 (chunks:[(title,uri)], usage:{prompt,output,total})。
    45s 上限：多角度會平行跑，單一角度逾時/失敗即放棄該角度（其他角度仍有結果），
    避免同步請求破 Cloudflare ~100s 代理上限（TypeError: Load failed 根因）。**刻意不重試。**
    token 在此「即時」取（_access_token 有快取+鎖：有效回快取、過期才 refresh）——避免長任務
    一開始取一次的 token 跑到後段角度才過期 → 401。把過期窗口從「整個任務」縮到「單次請求」。"""
    url = (f"https://aiplatform.googleapis.com/v1/projects/{project}"
           f"/locations/global/publishers/google/models/{_MODEL}:generateContent")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"temperature": 0.3},
    }
    try:
        token = _access_token()
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            d = json.load(r)
        cand = (d.get("candidates") or [{}])[0]
        gm = cand.get("groundingMetadata", {})
        chunks = [(c.get("web", {}).get("title", ""), c.get("web", {}).get("uri", ""))
                  for c in gm.get("groundingChunks", [])]
        um = d.get("usageMetadata", {}) or {}
        usage = {"prompt": int(um.get("promptTokenCount", 0) or 0),
                 "output": int(um.get("candidatesTokenCount", 0) or 0),
                 "total": int(um.get("totalTokenCount", 0) or 0)}
        return chunks, usage
    except Exception as e:
        print(f"[discover] grounding 失敗：{e}", flush=True)
        return [], {"prompt": 0, "output": 0, "total": 0}


def _is_safe_url(u: str) -> bool:
    """SSRF 防護：只允許 http/https，且 hostname 解析出的 IP 不得為
    私有 / loopback / link-local / reserved（擋 169.254.169.254、10.x、172.16.x、
    192.168.x、127.x、::1 等）。任何解析錯誤一律視為不安全。"""
    try:
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            return False
        host = p.hostname
        if not host:
            return False
        # 解析所有 A/AAAA 記錄，任一落在危險範圍即拒絕
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except Exception:
        return False


def _resolve(uri):
    """解析 vertexaisearch 轉址成真實 URL。

    只讀第一個 302 的 Location（即真實 URL），**不一路跟到最終網站**——
    否則會被「封鎖資料中心 IP」的站（myfone/cool-style 等）在 Cloud Run 上 HEAD 失敗而丟掉，
    而那些站本來就該交給爬蟲處理。多跟幾跳直到拿到非 vertexaisearch 的 URL。

    每次跟隨前先用 _is_safe_url 驗目標（SSRF 防護）：不安全就停止跟隨並回目前 URL
    （cur != uri 時回 cur，否則回 None）。失敗一律回 None。
    """
    cur = uri
    try:
        for _ in range(5):
            r = requests.head(cur, allow_redirects=False, timeout=8,
                              headers={"User-Agent": "Mozilla/5.0"})
            loc = r.headers.get("Location")
            if loc and r.status_code in (301, 302, 303, 307, 308):
                if loc.startswith("/"):
                    from urllib.parse import urljoin
                    loc = urljoin(cur, loc)
                # SSRF：驗證 Location 目標安全才跟隨；不安全就停止，回目前 URL
                if not _is_safe_url(loc):
                    return cur if cur != uri else None
                # 已跳離 vertexaisearch（拿到真實站 URL）→ 收工
                if "vertexaisearch.cloud.google.com" not in loc:
                    return loc
                cur = loc
                continue
            # 非轉址（200/4xx 等）：cur 已是最終 URL
            return cur if r.status_code < 400 else (cur if cur != uri else None)
        return cur if cur != uri else None
    except Exception:
        # 失敗一律回 None（避免丟出無法驗證的候選）
        return None


def discover(query: str, max_results: int = 50, angles=None) -> dict:
    """關鍵字 → 推薦爬取 URL 清單（多角度 grounding + 解析轉址 + 分類）。

    回 {status, query, count, by_source, usage:{prompt,output,total}, candidates:[...]}。
    candidates 項：{url, title, domain, source_type, region, flag}。TW 優先排序。
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return {"status": "failed", "error": "GOOGLE_CLOUD_PROJECT 未設定", "candidates": []}
    angles = angles or _DEFAULT_ANGLES

    # 多角度 grounding **平行**跑：牆鐘 ≈ 最慢一個角度，而非相加（壓在 Cloudflare ~100s 內）。
    # 單一角度 worker 例外時回 sentinel（空 chunks + 零 usage），不讓整批 map 中止。
    # token 由 _ground 內即時取（_access_token 快取，平行呼叫不會各打 metadata；過期自動 refresh）。
    def _ground_one(a):
        try:
            return _ground(project, a.format(q=query))
        except Exception as e:
            print(f"[discover] 角度 grounding 失敗：{e}", flush=True)
            return [], {"prompt": 0, "output": 0, "total": 0}

    raw = {}  # uri -> title
    usage_total = {"prompt": 0, "output": 0, "total": 0}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(angles)))) as ex:
        ground_results = list(ex.map(_ground_one, angles))
    for chunks, usage in ground_results:
        for t, u in chunks:
            if u and u not in raw:
                raw[u] = t
        for k in usage_total:
            usage_total[k] += usage[k]

    # 平行解析轉址；單筆 worker 例外回 sentinel None，不讓整批 map 中止
    def _resolve_one(kv):
        try:
            return _resolve(kv[0])
        except Exception:
            return None

    items = list(raw.items())
    rows, seen = [], set()
    with ThreadPoolExecutor(max_workers=10) as ex:
        for (uri, title), real in zip(items, ex.map(_resolve_one, items)):
            if real and real not in seen:
                seen.add(real)
                dom = urlparse(real).hostname or real
                rows.append({
                    "url": real, "title": title, "domain": dom,
                    "source_type": _source_type(real),
                    "region": _region(real), "flag": _flag(real),
                })

    rows.sort(key=lambda r: ({'TW': 0, '?': 1, 'HK': 2}.get(r["region"], 1),
                             1 if r["flag"] else 0))
    rows = rows[:max_results]

    from collections import Counter
    by_source = dict(Counter(r["source_type"] for r in rows))
    return {"status": "ok", "query": query, "count": len(rows),
            "by_source": by_source, "usage": usage_total, "candidates": rows}
