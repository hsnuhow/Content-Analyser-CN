# -*- coding: utf-8 -*-
"""
報告生成器

將三路分析結果（Path 1 數值 + Path 2 質化 + Synthesis 整合）
組裝成最終 Markdown 報告。

章節分工：
  § 1 摘要              → Synthesis LLM 生成
  § 2 TF-IDF 關鍵字表   → 程式直接生成（保證格式正確）
  § 3 語意主題分類      → 程式直接生成
  § 4 搜尋情境分析      → Synthesis LLM 生成
  § 5 LLM 質化分析      → Path 2b 直接輸出
  § 6 綜合洞察與建議    → Synthesis LLM 生成
  § 附錄                → 程式直接生成
"""
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from urllib.parse import urlparse

TAIPEI_TZ = timezone(timedelta(hours=8))


def _now_tw() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")


def _source_type_label(src: str) -> str:
    mapping = {
        "media": "媒體文章",
        "ecommerce": "電商產品頁",
        "forum": "論壇討論",
        "dcard": "Dcard",
        "youtube": "YouTube",
        "direct": "直接輸入",
    }
    return mapping.get(src, src)


# ──────────────────────────────────────────────────────────────────────
# 程式直接生成的章節
# ──────────────────────────────────────────────────────────────────────

def _section_tfidf(tfidf: Dict) -> str:
    top = tfidf.get("top_keywords", [])
    lines = [
        "## 2. 共同關鍵字（TF-IDF）",
        "",
        "跨所有內容，權重最高的詞彙代表這批素材的共同語彙核心。",
        "",
        "| 排名 | 關鍵字 | 權重 |",
        "| :--- | :----- | :--- |",
    ]
    for i, kw in enumerate(top[:25], 1):
        lines.append(f"| {i} | {kw['keyword']} | {kw['weight']:.4f} |")
    return "\n".join(lines)


def _section_clusters(clusters: Dict) -> str:
    groups = clusters.get("clusters", [])
    n = clusters.get("n_clusters", 0)
    if not groups or n == 0:
        return "## 3. 語意主題分類（語意分群）\n\n（語意分群未執行或結果不足）"

    lines = [
        "## 3. 語意主題分類（語意分群）",
        "",
        f"透過 Vertex AI 語意向量，將 {sum(len(g['articles']) for g in groups)} 篇內容聚類為 **{n} 個主題群**。",
        "",
    ]
    for g in groups:
        articles = g.get("articles", [])
        label = g.get("label", "")
        heading = f"### 主題群 {g['cluster_id'] + 1}"
        if label:
            heading += f"：{label}"
        heading += f"（{len(articles)} 篇）"
        lines.append(heading)
        lines.append("")
        if g.get("description"):
            lines.append(f"*{g['description']}*")
            lines.append("")
        if g.get("keywords"):
            lines.append("**代表詞彙：** " + "、".join(g["keywords"]))
            lines.append("")
        for a in articles:
            title = a.get("title") or a.get("url", "")
            url = a.get("url", "")
            if url:
                lines.append(f"- [{title}]({url})")
            else:
                lines.append(f"- {title}")
        lines.append("")
    return "\n".join(lines)


def _section_appendix(tfidf_per_article: List[Dict],
                       search_intents: List[Dict]) -> str:
    # 建立 search_intent 查詢 dict
    intent_map = {a.get("url", "") or a.get("title", ""): a for a in search_intents}

    lines = ["## 附錄：各篇搜尋情境與關鍵字", ""]
    for article in tfidf_per_article:
        title = article.get("title") or article.get("url", "")
        url = article.get("url", "")

        key = url or title
        intent_data = intent_map.get(key, {})
        intents = intent_data.get("search_intents", [])

        parsed = urlparse(url) if url else None
        safe_url = url if (parsed and parsed.scheme in ("http", "https")) else None
        if safe_url:
            lines.append(f"### [{title}]({safe_url})")
        else:
            lines.append(f"### {title}")
        lines.append("")

        if intents:
            lines.append("**搜尋情境：**")
            for si in intents:
                label = si.get("label", "")
                scenario = si.get("scenario", "")
                keywords = si.get("keywords", "")
                lines.append(f"- **{label}**：{scenario}  ")
                lines.append(f"  搜尋詞：`{keywords}`")
            lines.append("")

        kws = article.get("keywords", [])
        if kws:
            kw_str = "、".join(
                f"{k['keyword']}（{k['weight']:.4f}）" for k in kws[:5]
            )
            lines.append(f"**Top 關鍵字：** {kw_str}")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# 最終報告組裝
# ──────────────────────────────────────────────────────────────────────

def assemble(report_title: str,
             contents: List[Dict],
             nlp_results: Dict,
             llm_results: Dict,
             synthesis_parts: Dict,
             llm_provider: str,
             llm_model: str) -> str:
    """組裝最終 Markdown 報告。

    Args:
        synthesis_parts: {summary, search_intent_analysis, recommendations}
        nlp_results:     {tfidf, clusters}
        llm_results:     {search_intents, qualitative}
    """
    n = len(contents)
    source_counts: Dict[str, int] = {}
    for c in contents:
        st = _source_type_label(c.get("source_type", "未知"))
        source_counts[st] = source_counts.get(st, 0) + 1
    source_summary = "、".join(
        f"{st} {cnt} 篇" for st, cnt in source_counts.items()
    )

    sections = []

    # Header
    sections.append(
        f"# 受歡迎內容分析報告：{report_title}\n\n"
        f"*產生日期：{_now_tw()}　|　樣本數：{n} 篇　|　"
        f"語意模型：Vertex AI text-multilingual-embedding-002　|　"
        f"LLM：{llm_provider} / {llm_model}*"
    )

    # § 1 摘要（Synthesis LLM）
    sections.append(
        "## 1. 摘要\n\n"
        f"本報告分析 {n} 篇受歡迎內容，來源涵蓋 {source_summary}。"
        " 結合 TF-IDF 關鍵字、Vertex AI 語意分群與 LLM 質性分析。\n\n"
        + synthesis_parts.get("summary", "")
    )

    # § 2 TF-IDF（程式直接生成）
    sections.append(_section_tfidf(nlp_results.get("tfidf", {})))

    # § 3 語意分群（程式直接生成）
    sections.append(_section_clusters(nlp_results.get("clusters", {})))

    # § 4 搜尋情境分析（Synthesis LLM）
    sections.append(
        "## 4. 用戶搜尋情境分析\n\n"
        + synthesis_parts.get("search_intent_analysis", "（無法生成）")
    )

    # § 5 LLM 質化分析（Path 2b 直接輸出）
    sections.append(
        "## 5. LLM 質化分析\n\n"
        + llm_results.get("qualitative", "（無法生成）")
    )

    # § 6 綜合洞察與建議（Synthesis LLM）
    sections.append(
        "## 6. 綜合洞察與可操作建議\n\n"
        + synthesis_parts.get("recommendations", "（無法生成）")
    )

    # § 7 延伸關鍵字與內容缺口（語意延伸，Synthesis LLM）
    if synthesis_parts.get("expansion"):
        sections.append(
            "## 7. 延伸關鍵字與內容缺口（dataset 之外的相關機會）\n\n"
            "本節跳出本批內容，推論與同一群受眾相關、但這批 dataset 未直接涵蓋、"
            "可延伸經營的關鍵字、內容缺口與周邊主題。\n\n"
            + synthesis_parts.get("expansion", "")
        )

    # 附錄（程式直接生成）
    sections.append(
        _section_appendix(
            nlp_results.get("tfidf", {}).get("per_article", []),
            llm_results.get("search_intents", []),
        )
    )

    return "\n\n---\n\n".join(sections)
