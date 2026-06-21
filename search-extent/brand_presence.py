# -*- coding: utf-8 -*-
"""
search-extent 子功能 D：品牌聲量探勘（Brand Presence / Share-of-Voice）

回答「某品牌在某主題，有沒有 earned（第三方）聲量」——把「我找不到 GQ」這種
缺席洞察變成可量化、可重複的發現。指名去問（品牌錨定），而非掃描 top-N
（掃描本質上漏掉缺席者）。供給側、唯讀、無狀態、只回情報。

方法：對每個品牌做一次 Google Search grounding，請 Gemini（會實際讀頁面）判定
第三方聲量等級，並回依據來源；來源解析成真實 URL、分『自有 vs 第三方』。
"""
import os
import re
import json
import urllib.request
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

import discover as _d   # 重用 _access_token / _resolve / _source_type / is_configured

_MODEL = "gemini-2.5-flash"


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _ground_brand(project, topic, brand):
    """單一品牌錨定 grounding。回 (text, chunks[(title,uri)], usage)。逾時/失敗即放棄該品牌
    （由其他平行品牌補足結果），刻意不重試——避免破 Cloudflare 代理上限。
    token 在此即時取（_d._access_token 有快取+鎖）——避免長任務（多品牌）一開始取的 token
    跑到後段品牌才過期 → 401。"""
    prompt = (
        f"你在評估台灣市場的『內容聲量』。主題：「{topic}」，品牌：「{brand}」。\n"
        f"問題：針對這個主題，有沒有『第三方』（媒體 / 論壇 / 部落格 / YouTube，"
        f"非 {brand} 官方網站或官方賣場）明確提到並討論 {brand} 的評測、開箱、推薦或討論？\n\n"
        f"請『第一行』只輸出三選一判定（照抄其一）：\n"
        f"聲量:有\n聲量:僅自有\n聲量:缺席\n"
        f"判定標準——有：多個第三方明確提到該品牌；僅自有：只有品牌官方/賣場頁、無第三方；"
        f"缺席：幾乎找不到實質內容（連官方都很弱）。\n"
        f"第二行起，逐一列出你依據的具體網址。"
    )
    url = (f"https://aiplatform.googleapis.com/v1/projects/{project}"
           f"/locations/global/publishers/google/models/{_MODEL}:generateContent")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {"temperature": 0.2}}
    try:
        token = _d._access_token()
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=70) as r:
            d = json.load(r)
        cand = (d.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "")
                       for p in cand.get("content", {}).get("parts", []))
        gm = cand.get("groundingMetadata", {})
        chunks = [(c.get("web", {}).get("title", ""), c.get("web", {}).get("uri", ""))
                  for c in gm.get("groundingChunks", [])]
        um = d.get("usageMetadata", {}) or {}
        usage = {"prompt": int(um.get("promptTokenCount", 0) or 0),
                 "output": int(um.get("candidatesTokenCount", 0) or 0),
                 "total": int(um.get("totalTokenCount", 0) or 0)}
        return text, chunks, usage
    except Exception as e:
        print(f"[brand_presence] grounding 失敗（{brand}）：{e}", flush=True)
        return "", [], {"prompt": 0, "output": 0, "total": 0}


def _verdict(text: str) -> str:
    t = (text or "").replace("：", ":")
    if "聲量:有" in t:
        return "有聲量"
    if "僅自有" in t:
        return "僅自有"
    if "缺席" in t:
        return "缺席"
    return ""   # 未判定 → 由啟發式補


def _assess_one(project, topic, brand):
    text, chunks, usage = _ground_brand(project, topic, brand)
    slug = _slug(brand)
    srcs, seen, earned, official = [], set(), 0, False
    if chunks:
        # 內層池上限綁定實際 chunk 數：本函式本身已在外層「每品牌一條」的池裡跑
        #（brand_presence 對品牌開 min(8, len(brands)) 池），內層再固定開 8 會造成
        # 巢狀執行緒爆量（最壞 8×8）。綁 len(chunks) 後，chunk 少時不浪費執行緒。
        with ThreadPoolExecutor(max_workers=min(8, len(chunks))) as ex:
            reals = list(ex.map(lambda kv: _d._resolve(kv[1]), chunks))
    else:
        reals = []
    for (title, uri), real in zip(chunks, reals):
        if not real or real in seen:
            continue
        seen.add(real)
        dom = urlparse(real).hostname or real
        is_own = bool(slug) and slug in _slug(dom)
        if is_own:
            official = True
        else:
            earned += 1
        srcs.append({"url": real, "domain": dom,
                     "source_type": _d._source_type(real),
                     "kind": "own" if is_own else "earned"})
    level = _verdict(text)
    if not level:   # LLM 沒給格式 → 啟發式
        level = "有聲量" if earned >= 2 else ("僅自有" if official else "缺席")
    summary = (text or "").strip().split("\n")[0].replace("聲量:", "").replace("聲量：", "").strip()
    return {"brand": brand, "presence_level": level, "earned_count": earned,
            "official_present": official, "sources": srcs[:12],
            "summary": summary[:100]}, usage


def brand_presence(topic: str, brands, max_brands: int = 15) -> dict:
    """品牌清單 × 主題 → 各品牌 earned 聲量等級 + share-of-voice 排序。"""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return {"status": "failed", "error": "GOOGLE_CLOUD_PROJECT 未設定", "results": []}
    brands = [b.strip() for b in (brands or []) if b and b.strip()][:max_brands]
    if not topic.strip() or not brands:
        return {"status": "failed", "error": "缺少主題或品牌", "results": []}
    usage_total = {"prompt": 0, "output": 0, "total": 0}
    results = []

    # 單一品牌 worker 例外時回標記「探勘失敗」的 sentinel，不讓整批 map 中止（部分失敗仍回結果）。
    # token 由 _ground_brand 內即時取（_d._access_token 快取；過期自動 refresh，不會跑到後段品牌才 401）。
    def _assess_safe(b):
        try:
            return _assess_one(project, topic, b)
        except Exception as e:
            print(f"[brand_presence] 品牌探勘失敗（{b}）：{e}", flush=True)
            sentinel = {"brand": b, "presence_level": "探勘失敗", "earned_count": 0,
                        "official_present": False, "sources": [], "summary": "探勘失敗"}
            return sentinel, {"prompt": 0, "output": 0, "total": 0}

    with ThreadPoolExecutor(max_workers=min(8, len(brands))) as ex:
        for res, usage in ex.map(_assess_safe, brands):
            results.append(res)
            for k in usage_total:
                usage_total[k] += usage[k]
    order = {"有聲量": 0, "僅自有": 1, "缺席": 2}
    results.sort(key=lambda r: (order.get(r["presence_level"], 1), -r["earned_count"]))
    return {"status": "ok", "topic": topic, "count": len(results),
            "usage": usage_total, "results": results}
