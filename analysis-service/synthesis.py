# -*- coding: utf-8 -*-
"""
Synthesis LLM：整合數值報告 + 質化洞察，生成報告的詮釋性章節。

負責生成：
  - § 1 摘要（3–4 段，總結核心發現）
  - § 4 用戶搜尋情境分析（彙整 per-article 意圖，找出跨文章的情境模式）
  - § 6 綜合洞察與可操作建議（8–12 條，附佐證）
"""
import json
import re
from typing import List, Dict, Any

from llm_client import LLMClient

MAX_INTENT_SUMMARY = 15  # Synthesis 時最多傳入幾篇的 intent 摘要


def _clean_json(raw: str) -> str:
    """去除 markdown fence 並抽取最外層 JSON 物件。"""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return m.group(0) if m else raw


def label_clusters(clusters_dict: Dict, llm: LLMClient) -> None:
    """為每個語意主題群生成 LLM 描述（label + description），就地寫入各 cluster。

    一次 LLM 呼叫處理所有群（省成本）。失敗時不影響報告（群仍有代表詞彙）。
    """
    groups = clusters_dict.get("clusters", [])
    if not groups:
        return
    blocks = []
    for g in groups:
        titles = [(a.get("title") or a.get("url", ""))[:40] for a in g.get("articles", [])[:5]]
        kws = "、".join(g.get("keywords", [])[:8])
        blocks.append(f"群 {g['cluster_id'] + 1}：代表詞彙[{kws}]；文章[{' / '.join(titles)}]")

    prompt = (
        "以下是內容語意分群結果。請為每一群取一個精準的「主題標籤」（6–14 字，"
        "點出該群內容的共同主題/角色），並寫一句話描述該群的內容共通點與在整體中的定位。\n\n"
        + "\n".join(blocks)
        + "\n\n以 JSON 回傳（不要 markdown、不要說明）：\n"
          '{"labels":[{"id":1,"label":"主題標籤","desc":"一句話描述"}]}'
    )
    try:
        raw = llm.generate(prompt, temperature=0.3, max_tokens=1536)
        data = json.loads(_clean_json(raw))
        lm = {int(l.get("id")): l for l in data.get("labels", []) if l.get("id") is not None}
        for g in groups:
            l = lm.get(g["cluster_id"] + 1, {})
            if l.get("label"):
                g["label"] = l["label"]
            if l.get("desc"):
                g["description"] = l["desc"]
    except Exception as e:
        print(f"[Synthesis] 分群描述生成失敗（保留代表詞彙）：{e}", flush=True)


def _fmt_tfidf(tfidf: Dict) -> str:
    top = tfidf.get("top_keywords", [])[:20]
    return "Top 關鍵字：" + "、".join(
        f"{k['keyword']}({k['weight']:.4f})" for k in top
    )


def _fmt_clusters(clusters: Dict) -> str:
    groups = clusters.get("clusters", [])
    lines = [f"語意群組（共 {clusters.get('n_clusters', 0)} 群）："]
    for g in groups:
        articles = g.get("articles", [])
        titles = [a.get("title", a.get("url", ""))[:25] for a in articles[:3]]
        suffix = "…" if len(articles) > 3 else ""
        lines.append(f"  群 {g['cluster_id'] + 1}（{len(articles)} 篇）：{'、'.join(titles)}{suffix}")
    return "\n".join(lines)


def _fmt_search_extent(search_extent_results: Dict) -> str:
    """把 search-extent 真實關聯關鍵字整理成 prompt 區塊（依群分組）。"""
    if not search_extent_results:
        return ""
    lines = []
    for cid in sorted(search_extent_results.keys()):
        data = search_extent_results[cid]
        label = data.get("label") or f"群 {cid + 1}"
        seeds = "、".join(data.get("seeds", []))
        lines.append(f"\n【{label}】（種子：{seeds}）")
        for idea in data.get("ideas", [])[:20]:
            vol = idea.get("avg_monthly_searches")
            comp = idea.get("competition") or "-"
            vol_s = f"{vol:,}/月" if isinstance(vol, int) else "量級未知"
            lines.append(f"  - {idea.get('text', '')}（{vol_s}，競爭 {comp}）")
    return "\n".join(lines)


def _fmt_intents(search_intents: List[Dict]) -> str:
    lines = []
    for a in search_intents[:MAX_INTENT_SUMMARY]:
        title = (a.get("title") or a.get("url", ""))[:30]
        for si in a.get("search_intents", [])[:2]:
            lines.append(f"- [{title}] {si.get('label', '')}：{si.get('keywords', '')}")
    return "\n".join(lines) if lines else "（無搜尋意圖資料）"


def run(nlp_results: Dict, llm_results: Dict,
        report_title: str, n_articles: int,
        llm: LLMClient, search_extent_results: Dict = None) -> Dict[str, str]:
    """
    呼叫 Synthesis LLM 生成詮釋性章節。

    search_extent_results：{cluster_id: {label, seeds, ideas}}，由 search-extent 提供的
    真實 Google 關聯關鍵字 + 搜尋量。有資料時 §7 改走「真實資料接地」版本。

    回傳：{summary, search_intent_analysis, recommendations, expansion}
    """
    search_extent_results = search_extent_results or {}
    tfidf_summary = _fmt_tfidf(nlp_results.get("tfidf", {}))
    cluster_summary = _fmt_clusters(nlp_results.get("clusters", {}))
    intent_summary = _fmt_intents(llm_results.get("search_intents", []))
    qualitative = llm_results.get("qualitative", "")[:3000]  # 避免 prompt 過長

    base_context = f"""以下是針對「{report_title}」分析 {n_articles} 篇內容的結果：

【數值分析（Path 1）】
{tfidf_summary}

{cluster_summary}

【各篇搜尋意圖摘要（Path 2a）】
{intent_summary}

【六面向質化分析（Path 2b，節錄）】
{qualitative}"""

    # ── § 1 摘要 ──
    summary_prompt = f"""{base_context}

請根據以上分析，用 3–4 段正體中文，寫出本次報告的「執行摘要」。
摘要應說明：這批內容的核心語彙是什麼、主要呈現哪幾個主題群、\
最值得關注的質化洞察是什麼。
語氣精確、有說服力，像是給行銷主管看的一頁式報告開頭。
直接輸出段落，不要標題。"""
    try:
        summary = llm.generate(summary_prompt, temperature=0.3, max_tokens=2048)
    except Exception as e:
        print(f"[Synthesis] § 1 摘要生成失敗：{e}", flush=True)
        summary = "（摘要生成失敗，請重新分析）"

    # ── § 4 搜尋情境分析 ──
    intent_prompt = f"""{base_context}

根據上方各篇文章的搜尋意圖資料，請整合出跨文章的「用戶搜尋情境模式」。

找出最常見的 5–8 個情境，每個情境用以下格式：

**情境 N：[情境標籤]**（覆蓋 X 篇內容）
- 用戶狀態：[用戶在什麼需求或狀態下]
- 代表搜尋詞組：[2–4 個典型的搜尋詞組]
- 內容特徵：[這類搜尋找到的內容有什麼共同特點]

直接輸出以上格式，不要前言或後記。"""
    try:
        search_intent_analysis = llm.generate(intent_prompt, temperature=0.3, max_tokens=3072)
    except Exception as e:
        print(f"[Synthesis] § 4 搜尋情境分析失敗：{e}", flush=True)
        search_intent_analysis = "（搜尋情境分析生成失敗，請重新分析）"

    # ── § 6 綜合建議 ──
    rec_prompt = f"""{base_context}

根據數值分析與質化洞察，請提出 8–12 條「可操作的內容策略建議」。

每條建議格式：
**[建議標題]**
說明：[具體做法，2–3 句]
佐證：[引用數值或質化分析的具體依據]

建議應涵蓋：訴求切角、關鍵字使用、內容格式、標題公式、平台策略等面向。
直接輸出建議清單，不要前言或後記。"""
    try:
        recommendations = llm.generate(rec_prompt, temperature=0.3, max_tokens=4096)
    except Exception as e:
        print(f"[Synthesis] § 6 建議生成失敗：{e}", flush=True)
        recommendations = "（建議生成失敗，請重新分析）"

    # ── § 7 延伸關鍵字與內容缺口（對應方法論二：找差異化 gap）──
    # 有 search-extent 真實資料 → 走「真實資料接地」版；否則退回純語意推論版。
    se_block = _fmt_search_extent(search_extent_results)
    if se_block:
        expansion_prompt = f"""{base_context}

【真實搜尋接地資料（Google Ads Keyword Planner，依語意群分組）】
以下是用各語意群的核心關鍵字向 Google 查得的「真實關聯關鍵字 + 月均搜尋量 + 競爭度」。\
這是市場端真實的需求訊號（不是推論）：
{se_block}

請結合「本批內容的分析」與「上述真實搜尋資料」，產出延伸建議。\
務必以真實搜尋資料為證據，標出搜尋量，並判斷哪些是本批內容「沒涵蓋但有真實需求」的缺口。

請用以下三段格式輸出（正體中文，直接輸出，不要前言後記）：

### 延伸關鍵字（有真實搜尋需求、但本批內容未涵蓋或著墨不足）
列出 10–15 個，每個用 `- 關鍵字（約 X/月）— 為何相關、本批是否已涵蓋` 格式。\
優先列「真實搜尋量高 + 本批沒寫到」的詞，這些是最值得補的流量機會。

### 內容缺口（差異化切點，以真實需求為證）
找出 4–6 個「真實搜尋資料顯示受眾在意、但現有內容沒切到」的角度，引用具體關鍵字與搜尋量佐證。

### 可延伸的周邊主題
列出 3–5 個與核心主題相鄰、可擴展成內容矩陣的周邊主題（可參考真實關聯詞），簡述延伸邏輯。

註明：本節延伸關鍵字含 Google 真實搜尋量佐證；周邊主題部分含推論。"""
        expansion_temp = 0.4
    else:
        expansion_prompt = f"""{base_context}

以上是本批內容「內部」的分析。現在請你跳出這批 dataset，運用你對此主題與受眾的知識，\
推論「這批內容之外、但與同一群受眾高度相關、值得延伸經營」的內容機會。\
重點是找出 dataset 沒有直接涵蓋、但其實相關可延伸的關鍵字與主題。

請用以下三段格式輸出（正體中文，直接輸出，不要前言後記）：

### 延伸關鍵字（相關但本批內容未涵蓋或著墨不足）
列出 8–15 個「同一群受眾也會搜尋、但這批內容沒出現或只略提」的關鍵字／搜尋詞組，\
每個用 `- 關鍵字 — 為何與此受眾相關（一句）` 格式。優先列搜尋意圖明確、可帶來新流量的詞。

### 內容缺口（差異化切點）
找出 4–6 個「受眾在意、但現有內容沒說清楚或沒切到」的角度，每個說明缺口是什麼、為何是好的差異化機會。

### 可延伸的周邊主題
列出 3–5 個與核心主題相鄰、可擴展成內容矩陣的周邊主題，並簡述延伸邏輯。

註明：本節為基於受眾知識的「推論延伸」，非 dataset 內實際出現，建議再以實際搜尋資料（如 Google 相關搜尋/Trends）驗證。"""
        expansion_temp = 0.5
    try:
        expansion = llm.generate(expansion_prompt, temperature=expansion_temp, max_tokens=3072)
    except Exception as e:
        print(f"[Synthesis] § 7 延伸關鍵字生成失敗：{e}", flush=True)
        expansion = "（延伸關鍵字分析生成失敗，請重新分析）"

    return {
        "summary": summary,
        "search_intent_analysis": search_intent_analysis,
        "recommendations": recommendations,
        "expansion": expansion,
    }
