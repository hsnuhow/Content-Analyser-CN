# -*- coding: utf-8 -*-
"""
Synthesis LLM：整合數值報告 + 質化洞察，生成報告的詮釋性章節。

負責生成：
  - § 1 摘要（3–4 段，總結核心發現）
  - § 4 用戶搜尋情境分析（彙整 per-article 意圖，找出跨文章的情境模式）
  - § 6 綜合洞察與可操作建議（8–12 條，附佐證）
"""
import json
from typing import List, Dict, Any

from llm_client import LLMClient

MAX_INTENT_SUMMARY = 15  # Synthesis 時最多傳入幾篇的 intent 摘要


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


def _fmt_intents(search_intents: List[Dict]) -> str:
    lines = []
    for a in search_intents[:MAX_INTENT_SUMMARY]:
        title = (a.get("title") or a.get("url", ""))[:30]
        for si in a.get("search_intents", [])[:2]:
            lines.append(f"- [{title}] {si.get('label', '')}：{si.get('keywords', '')}")
    return "\n".join(lines) if lines else "（無搜尋意圖資料）"


def run(nlp_results: Dict, llm_results: Dict,
        report_title: str, n_articles: int,
        llm: LLMClient) -> Dict[str, str]:
    """
    呼叫 Synthesis LLM 生成三個詮釋性章節。

    回傳：{summary, search_intent_analysis, recommendations}
    """
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
        summary = llm.generate(summary_prompt, temperature=0.3, max_tokens=1024)
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
        search_intent_analysis = llm.generate(intent_prompt, temperature=0.3, max_tokens=2048)
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
        recommendations = llm.generate(rec_prompt, temperature=0.3, max_tokens=3072)
    except Exception as e:
        print(f"[Synthesis] § 6 建議生成失敗：{e}", flush=True)
        recommendations = "（建議生成失敗，請重新分析）"

    # ── § 7 延伸關鍵字與內容缺口（語意延伸，dataset 之外的差異化機會）──
    # 對應產品方法論二：找「閱聽眾在意、但現有內容沒說好」的 gap。
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
    try:
        expansion = llm.generate(expansion_prompt, temperature=0.5, max_tokens=2560)
    except Exception as e:
        print(f"[Synthesis] § 7 延伸關鍵字生成失敗：{e}", flush=True)
        expansion = "（延伸關鍵字分析生成失敗，請重新分析）"

    return {
        "summary": summary,
        "search_intent_analysis": search_intent_analysis,
        "recommendations": recommendations,
        "expansion": expansion,
    }
