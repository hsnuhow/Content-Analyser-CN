# -*- coding: utf-8 -*-
"""
選擇器研究 Agent（on-demand，用戶觸發，與爬蟲不並行）

當資料集爬完出現失敗項，用戶可呼叫此工具對失敗網域做「閉環式研究」：
  開樣本 → 給 Gemini DOM 摘要 → 模型提選擇器 → 【實測該選擇器抽到幾字/像不像正文】
  → 不夠好就回饋、讓模型再修（最多 N 步）→ 過關後用第二樣本交叉驗證
  → 產出候選選擇器模板 或 失敗診斷。

設計重點：
- 重用 crawler.HeadlessCrawler 的 Chrome / DOM 摘要 / 評估工具，不肥大主爬蟲、不重複。
- 閉環（提出→實測→修正），非單次盲射。步數/時間上限控成本。
- 結果 per-domain 寫 selector_candidates，待 admin 確認才升級（site_learning）。
- LLM 用系統 GENAI_API_KEY（研究屬系統維護，非 per-project）。
"""
import json
import os
import re
import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from crawler import HeadlessCrawler
from site_learning import (detect_cms, save_selector_candidate,
                           normalize_selector, _is_valid_selector)
from page_classify import (looks_like_browser_error_page, looks_like_http_error_page,
                           looks_like_block_page)

MAX_STEPS = 6            # 每網域最多問模型幾次（提出→實測→修正的回合數）
PER_DOMAIN_BUDGET = 120  # 每網域研究時間上限（秒）
MAX_DOMAINS = 10         # 單次研究最多幾個網域（控成本/時間）
MIN_GOOD_CHARS = 300     # 選擇器抽出視為「有效正文」的最低字數
AUTO_PROMOTE_MIN_CHARS = 800  # P2：自動升級 learned 的最低驗證字數（高於候選門檻，保守）


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


def _group_by_domain(urls: List[str]) -> "OrderedDict[str, List[str]]":
    """把失敗 URL 依網域分組（每組保留出現順序，去重）。"""
    groups: "OrderedDict[str, List[str]]" = OrderedDict()
    for u in urls:
        u = (u or "").strip()
        if not u:
            continue
        d = _domain_of(u)
        if not d:
            continue
        groups.setdefault(d, [])
        if u not in groups[d]:
            groups[d].append(u)
    return groups


def _ask_gemini_for_selector(genai_key: str, dom_summary: List[Dict],
                             cms: str, tried: List[Dict], title: str,
                             usage_sink: list = None) -> Optional[str]:
    """請 Gemini 依 DOM 摘要 + 既往嘗試回饋，提出「下一個」要試的主文選擇器（CSS）。
    回 selector 字串或 None。usage_sink：給 list 則記系統付 token（選擇器研究，系統 GENAI Key）。"""
    if not genai_key:
        return None
    try:
        from google import genai
        from google.genai import types
    except Exception:
        return None

    tried_block = ""
    if tried:
        lines = [f"  - `{t['selector']}` → 抽到 {t['chars']} 字"
                 f"{'（疑似列表頁）' if t.get('is_listing') else ''}"
                 f"{'（疑似 cookie 區塊）' if t.get('is_cookie') else ''}"
                 for t in tried]
        tried_block = ("\n已經試過、但不理想的選擇器（請避開、提出不同的）：\n"
                       + "\n".join(lines) + "\n")

    prompt = (
        "你是網頁主文抽取專家。以下是某文章頁的 DOM 結構摘要（每個候選節點含 css_path、"
        "文字長度、段落數、連結密度、預覽）。請找出**最可能是文章正文容器**的 CSS 選擇器。\n"
        "⚠️ 選擇器必須能**套用到同網域的其他文章**（這是要建立網域模板）：\n"
        "  - **絕對避免**含文章編號/日期的 id 或 class（如 `#container94110`、`#post-12345`）——只會匹配單篇。\n"
        "  - 優先用穩定的 class（如 `.article-content`、`.entry-content`）、語意標籤（`article`）或結構選擇器。\n"
        "  - 選正文容器本身，不要選含大量『延伸閱讀/相關文章』卡片的最外層 wrapper。\n"
        f"頁面標題：{title}\nCMS 指紋：{cms}\n{tried_block}\n"
        "DOM 摘要：\n"
        + json.dumps(dom_summary, ensure_ascii=False)[:28000]
        + "\n\n只回 JSON（不要 markdown、不要說明）：{\"selector\":\"CSS選擇器\",\"reason\":\"一句話\"}"
    )
    try:
        client = genai.Client(api_key=genai_key)
        used_model = "gemini-2.5-flash"
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=types.GenerateContentConfig(temperature=0.3))
        except Exception:
            used_model = "gemini-2.5-flash-lite"
            resp = client.models.generate_content(
                model="gemini-2.5-flash-lite", contents=prompt,
                config=types.GenerateContentConfig(temperature=0.3))
        # 系統付 token 記帳（選擇器研究）：抓 usage_metadata
        if usage_sink is not None:
            try:
                um = getattr(resp, "usage_metadata", None)
                usage_sink.append({
                    "category": "selector_research", "provider": "gemini", "model": used_model,
                    "prompt": int(getattr(um, "prompt_token_count", 0) or 0),
                    "output": int(getattr(um, "candidates_token_count", 0) or 0),
                    "total": int(getattr(um, "total_token_count", 0) or 0)})
            except Exception:
                pass
        text = (getattr(resp, "text", None) or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0) if m else text)
        sel = (data.get("selector") or "").strip()
        return sel or None
    except Exception:
        return None


def _eval_selector(crawler: HeadlessCrawler, soup: BeautifulSoup, selector: str) -> Dict:
    """在已渲染的 soup 上實測選擇器，回 {matched, chars, is_listing, is_cookie, preview}。"""
    try:
        node = soup.select_one(selector)
    except Exception:
        return {"matched": False, "chars": 0, "is_listing": False, "is_cookie": False,
                "is_error": False, "preview": ""}
    if not node:
        return {"matched": False, "chars": 0, "is_listing": False, "is_cookie": False,
                "is_error": False, "preview": ""}
    text = crawler._clean_text(node.get_text("\n", strip=True))
    return {
        "matched": True,
        "chars": len(text),
        "is_listing": bool(crawler._looks_like_listing_block(node)),
        "is_cookie": bool(crawler._looks_like_cookie_banner(text, node)),
        # 抽到的是否為瀏覽器錯誤頁 / 反爬挑戰頁殘片（被擋站常見：抓到 Cloudflare 挑戰頁的少量字，
        # 字數剛好過門檻卻不是真內文）→ 不可收為候選。
        "is_error": bool(looks_like_browser_error_page(text) or looks_like_block_page(text)),
        "preview": text[:160],
    }


def _classify_failure(crawler: HeadlessCrawler, html: str, best_chars: int,
                      best_is_listing: bool, best_is_error: bool = False) -> str:
    """怎麼試都找不到有效選擇器時，分類失敗原因（給維運判斷下一步）。"""
    text_len = len(re.sub(r"<[^>]+>", " ", html or ""))
    # best_is_error：抽取階段在「抽出內容」上偵測到的錯誤/挑戰頁（比對 raw HTML 可靠，
    # 因 looks_like_*_page 對長 raw HTML 有長度上限）；輔以 http_error 標記比對。
    if best_is_error or looks_like_http_error_page((html or "")[:2000]):
        return ("blocked_or_error：頁面回 403/反爬挑戰頁/錯誤頁（網站封鎖爬蟲）"
                "→ 建議啟用 Tier 3 住宅代理重試，或用 Cowork 真實瀏覽器手動蒐集")
    if text_len < 800:
        return "js_empty：渲染後內容極少（SPA/需登入/嚴重反爬）→ 需更強渲染或 Tier 2/3"
    if best_is_listing:
        return "listing_page：抓到的最佳區塊像分類/列表頁，非單篇文章 → 確認 URL 是否為文章頁"
    return "no_extractable_article：有內容但找不到明確正文容器 → 可能版型特殊，建議人工加 SITE_TEMPLATE"


def research_domain(domain: str, sample_urls: List[str],
                    log: Callable[[str], None], usage_sink: list = None) -> Dict:
    """對單一網域跑研究 agent 閉環。回 {domain, selector?, validated_chars?, cms, diagnosis?, samples}。
    usage_sink：系統付 token 記帳（選擇器研究）累加。"""
    genai_key = os.environ.get("GENAI_API_KEY")
    samples = sample_urls[:2]
    crawler = HeadlessCrawler(log_callback=log)
    deadline = time.time() + PER_DOMAIN_BUDGET
    result = {"domain": domain, "samples": samples, "cms": "", "selector": None,
              "validated_chars": 0, "diagnosis": None}
    try:
        crawler._init_driver()
        # 樣本 1：開頁、取渲染後 DOM
        crawler._open(samples[0])
        html1 = crawler.driver.page_source
        soup1 = BeautifulSoup(html1, "html.parser")
        result["cms"] = detect_cms(html1)
        dom_summary = crawler._build_dom_summary(soup1)
        title = (soup1.title.get_text(strip=True) if soup1.title else "") or domain

        tried: List[Dict] = []
        chosen = None
        best_chars, best_listing, best_error = 0, False, False
        for step in range(MAX_STEPS):
            if time.time() > deadline:
                log(f"[Research] {domain} 達時間上限，停止")
                break
            sel = _ask_gemini_for_selector(genai_key, dom_summary, result["cms"], tried, title,
                                           usage_sink=usage_sink)
            if not sel:
                break
            ev = _eval_selector(crawler, soup1, sel)
            tried.append({"selector": sel, **ev})
            log(f"[Research] {domain} 第{step+1}步：`{sel}` → {ev['chars']}字"
                f"{'（列表）' if ev['is_listing'] else ''}{'（cookie）' if ev['is_cookie'] else ''}")
            if ev["matched"] and ev["chars"] > best_chars:
                best_chars, best_listing = ev["chars"], ev["is_listing"]
            if ev.get("is_error"):       # 任一步抓到錯誤/挑戰頁 → 標記（供失敗診斷判 blocked）
                best_error = True
            # 接受條件（候選階段就把關，避免吐弱候選浪費 admin 審查）：非 cookie、
            #   **非錯誤/挑戰頁殘片**（被擋站會抓到 Cloudflare 挑戰頁少量字）、
            #   **選擇器可泛化**（非 main/body/article 等過寬、非原子類/數字 id；learned 階段本來也會拒），
            #   且（內文很多→即使含相關連結也算文章 ‖ 內文足量且非列表）。
            #   放寬 is_listing 硬拒：真文章容器常含「延伸閱讀」等連結，會誤判列表。
            sel_generalizable = bool(_is_valid_selector(normalize_selector(sel)))
            if (ev["matched"] and not ev["is_cookie"] and not ev["is_error"]
                    and sel_generalizable and (
                    ev["chars"] >= 1000 or
                    (ev["chars"] >= MIN_GOOD_CHARS and not ev["is_listing"]))):
                chosen = sel
                break

        if not chosen:
            result["diagnosis"] = _classify_failure(crawler, html1, best_chars,
                                                     best_listing, best_error)
            log(f"[Research] {domain} 未找到有效選擇器 → {result['diagnosis']}")
            return result

        # 樣本 2 交叉驗證（有第二個樣本才做）
        val_chars = best_chars
        if len(samples) > 1:
            try:
                crawler._open(samples[1])
                soup2 = BeautifulSoup(crawler.driver.page_source, "html.parser")
                ev2 = _eval_selector(crawler, soup2, chosen)
                log(f"[Research] {domain} 樣本2驗證：`{chosen}` → {ev2['chars']}字")
                if not (ev2["matched"] and ev2["chars"] >= MIN_GOOD_CHARS
                        and not ev2["is_listing"] and not ev2["is_cookie"]):
                    # 第二樣本沒過 → 仍記為候選但標較低信心（admin 判斷）
                    result["diagnosis"] = "single_sample_only：僅單樣本通過，跨樣本未一致，建議人工確認"
                else:
                    val_chars = min(val_chars, ev2["chars"])
            except Exception as e:
                log(f"[Research] {domain} 樣本2驗證略過：{e}")

        result["selector"] = chosen
        result["validated_chars"] = val_chars
        return result
    except Exception as e:
        result["diagnosis"] = f"research_error：{e}"
        return result
    finally:
        try:
            crawler.close()
        except Exception:
            pass


def _write_selector_usage(records: list) -> None:
    """選擇器研究的系統付 token → system_token_usage（與 analysis 端同 schema，供後台統一彙整）。"""
    recs = [r for r in (records or []) if r.get("total")]
    if not recs:
        return
    try:
        from site_learning import _client
        from firebase_admin import firestore
        by_cat = {"selector_research": {"prompt": 0, "output": 0, "total": 0, "calls": 0}}
        tot = {"prompt": 0, "output": 0, "total": 0}
        model = ""
        for r in recs:
            for k in ("prompt", "output", "total"):
                by_cat["selector_research"][k] += int(r.get(k, 0) or 0)
                tot[k] += int(r.get(k, 0) or 0)
            by_cat["selector_research"]["calls"] += 1
            model = r.get("model", model)
        _client().collection("system_token_usage").add({
            "payer": "system", "service": "content-crawler",
            "job_kind": "selector_research", "job_id": "", "project_id": "",
            "by_category": by_cat, "model": model,
            "prompt_tokens": tot["prompt"], "output_tokens": tot["output"],
            "total_tokens": tot["total"], "embedding": None,
            "at": firestore.SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"[Research] 選擇器 token 記帳略過：{e}", flush=True)


def run_research(urls: List[str], log: Callable[[str], None]) -> Dict:
    """研究入口：對失敗 URL 依網域分組，逐網域跑 agent，產出候選/診斷並寫 Firestore。
    回 {candidates:[...], diagnoses:[...], domains_researched:n}。"""
    groups = _group_by_domain(urls)
    domains = list(groups.keys())[:MAX_DOMAINS]
    candidates, diagnoses = [], []
    usage_recs: list = []   # 系統付 token 記帳（選擇器研究）
    for i, domain in enumerate(domains):
        log(f"[Research] ({i+1}/{len(domains)}) 研究網域 {domain} …")
        r = research_domain(domain, groups[domain], log, usage_sink=usage_recs)
        if r.get("selector"):
            # P2：高信心 → 自動升級 learned，省 admin 介入（少介入）。判定：2+ 樣本跨樣本驗證、
            #   無診斷（2nd 樣本過）、字數足。選擇器已過 P3 正規化把關；save_learned_selector 再驗一次
            #   （拒過寬/原子類/數字 id）；讀取端 Phase2 cache-hit（≥300字/非列表/非cookie）+ self-heal
            #   為安全網 → 即使誤升級也不會被盲套污染。其餘仍待 admin 核准。
            high_conf = (len(r.get("samples", [])) >= 2 and not r.get("diagnosis")
                         and r.get("validated_chars", 0) >= AUTO_PROMOTE_MIN_CHARS)
            if high_conf:
                from site_learning import save_learned_selector
                save_learned_selector(domain, r["selector"], (r.get("samples") or [""])[0],
                                      r.get("validated_chars", 0), r.get("cms", ""))
                save_selector_candidate(
                    domain, [r["selector"]], cms=r.get("cms", ""),
                    validated_chars=r.get("validated_chars", 0), sample_urls=r.get("samples", []),
                    diagnosis="auto_approved：高信心（跨樣本驗證+字數足）自動升級為已學",
                    status="auto_approved")
                r["auto_promoted"] = True
                log(f"[Research] {domain} 高信心 → 自動升級為已學（{r.get('validated_chars', 0)}字，跳過 admin）")
            else:
                save_selector_candidate(
                    domain, [r["selector"]], cms=r.get("cms", ""),
                    validated_chars=r.get("validated_chars", 0),
                    sample_urls=r.get("samples", []), diagnosis=r.get("diagnosis") or "")
            candidates.append(r)
        else:
            diagnoses.append({"domain": domain, "diagnosis": r.get("diagnosis"),
                              "samples": r.get("samples", [])})
    _write_selector_usage(usage_recs)   # 系統付 token → 後台
    n_auto = sum(1 for c in candidates if c.get("auto_promoted"))
    return {"candidates": candidates, "diagnoses": diagnoses,
            "domains_researched": len(domains), "auto_promoted": n_auto,
            "domains_skipped": max(0, len(groups) - len(domains))}
