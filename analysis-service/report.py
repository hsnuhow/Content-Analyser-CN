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
import csv
import io
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from urllib.parse import urlparse

TAIPEI_TZ = timezone(timedelta(hours=8))


def _now_tw() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")


# ──────────────────────────────────────────────────────────────────────
# 數值分析 CSV 匯出（供獨立下載核實；與報告內 §2/§3.1/§3.2 表格並存）
# ──────────────────────────────────────────────────────────────────────

def _csv_safe(cell):
    """CSV 公式注入防護：cell 若以 = + - @ 或控制字元（Tab/CR）開頭，前置單引號中和。
    內容（keyword/entity/itemset）源自不可信的爬取/匯入文字，分析師以 Excel/Sheets 開啟
    時，= 開頭的 cell 會被當公式求值（HYPERLINK 外洩、舊版 DDE）。非字串原樣回傳。"""
    if isinstance(cell, str) and cell and cell[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + cell
    return cell


def _to_csv(header: List[str], rows: List[List]) -> str:
    """以 csv 模組產生正確跳脫的 CSV 字串（含逗號/引號的詞自動加引號）。
    每個 cell 另過 _csv_safe，中和公式注入。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_csv_safe(c) for c in header])
    w.writerows([[_csv_safe(c) for c in row] for row in rows])
    return buf.getvalue()


def build_numeric_exports(nlp_results: Dict) -> Dict[str, str]:
    """把三項數值分析結果各自輸出成 CSV 字串，供前端獨立下載核實。
    回 {tfidf, association, entities}（皆為 CSV 文字；無資料則為僅含表頭的空表）。
    """
    # 1) TF-IDF：rank, keyword, weight
    tf = (nlp_results.get("tfidf") or {}).get("top_keywords", []) or []
    tfidf_csv = _to_csv(
        ["rank", "keyword", "weight"],
        [[i, k.get("keyword", ""), k.get("weight", "")]
         for i, k in enumerate(tf, 1)],
    )

    # 2) 關聯規則：itemset 與 rule 兩類同表（type 區分）
    assoc = nlp_results.get("assoc") or {}
    arows: List[List] = []
    for s in (assoc.get("itemsets") or []):
        arows.append(["itemset", " + ".join(s.get("items", [])), "",
                      s.get("support", ""), "", "", s.get("count", "")])
    for r in (assoc.get("rules") or []):
        arows.append(["rule", r.get("antecedent", ""), r.get("consequent", ""),
                      r.get("support", ""), r.get("confidence", ""),
                      r.get("lift", ""), r.get("count", "")])
    association_csv = _to_csv(
        ["type", "antecedent_or_items", "consequent",
         "support", "confidence", "lift", "count"],
        arows,
    )

    # 3) Cloud NL 實體 + 情感：前段為整體情感 meta，後段為實體表（兩區塊堆疊）
    ent = nlp_results.get("entities") or {}
    meta_block = _to_csv(
        ["metric", "value"],
        [["enabled", ent.get("enabled", False)],
         ["n_docs", ent.get("n_docs", "")],
         ["avg_sentiment", ent.get("avg_sentiment", "")]],
    )
    ent_block = _to_csv(
        ["entity", "type", "salience", "mentions"],
        [[e.get("name", ""), e.get("type", ""), e.get("salience", ""),
          e.get("mentions", "")] for e in (ent.get("entities") or [])],
    )
    entities_csv = meta_block + "\n" + ent_block

    return {"tfidf": tfidf_csv, "association": association_csv,
            "entities": entities_csv}


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
    for i, kw in enumerate(top[:50], 1):
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
            # 與附錄一致：只允許 http(s) scheme 的連結，擋 javascript: 等注入
            parsed = urlparse(url) if url else None
            safe_url = url if (parsed and parsed.scheme in ("http", "https")) else None
            if safe_url:
                lines.append(f"- [{title}]({safe_url})")
            else:
                lines.append(f"- {title}")
        lines.append("")
    return "\n".join(lines)


def _section_assoc(assoc: Dict) -> str:
    """關聯規則探勘：高頻共現組合 + 關聯規則（support/confidence/lift）。
    無資料回空字串（assemble 會略過）。重點是「哪些主題詞固定一起出現」。"""
    if not assoc:
        return ""
    rules = assoc.get("rules", [])
    itemsets = assoc.get("itemsets", [])
    if not rules and not itemsets:
        return ""
    lines = [
        "## 3.1 主題關聯規則（共現探勘）",
        "",
        "以各篇核心關鍵字為「品項」做頻繁共現分析，找出固定一起出現的詞組。"
        "`lift > 1` 代表兩者同時出現的頻率高於隨機——是內容選題時可成套操作的組合。",
        "",
    ]
    if itemsets:
        lines += [
            "**高頻共現組合**",
            "",
            "| 共現詞組 | 支持度 | 篇數 |",
            "| :--- | ---: | ---: |",
        ]
        for s in itemsets[:12]:
            items = " ＋ ".join(s.get("items", []))
            lines.append(f"| {items} | {s.get('support')} | {s.get('count')} |")
        lines.append("")
    if rules:
        lines += [
            "**關聯規則**（A → B：內容提到 A 時，也提到 B 的傾向）",
            "",
            "| 規則 | 信賴度 | lift | 篇數 |",
            "| :--- | ---: | ---: | ---: |",
        ]
        for r in rules[:15]:
            lines.append(f"| {r.get('antecedent')} → {r.get('consequent')} | "
                         f"{r.get('confidence')} | {r.get('lift')} | {r.get('count')} |")
        lines.append("")
    return "\n".join(lines)


def _section_entities(entities: Dict) -> str:
    """Cloud NL 實體 salience + 整體情感。未啟用 / 無資料回空字串。"""
    if not entities or not entities.get("enabled"):
        return ""
    ents = entities.get("entities", [])
    if not ents:
        return ""
    lines = [
        "## 3.2 關鍵實體與情感（Cloud Natural Language）",
        "",
        f"以 Google Cloud NL 對 {entities.get('n_docs')} 篇做實體抽取與情感分析。"
        "`salience` 為實體在文本中的重要度（越高越核心）。",
        "",
    ]
    avg = entities.get("avg_sentiment")
    if avg is not None:
        tone = "偏正向" if avg > 0.15 else ("偏負向" if avg < -0.15 else "中性")
        lines.append(f"**整體情感分數：{avg}**（{tone}；範圍 −1～+1）")
        lines.append("")
    lines += [
        "| 實體 | 類型 | salience | 提及次數 |",
        "| :--- | :--- | ---: | ---: |",
    ]
    for e in ents[:20]:
        lines.append(f"| {e.get('name')} | {e.get('type')} | "
                     f"{e.get('salience')} | {e.get('mentions')} |")
    lines.append("")
    return "\n".join(lines)


def _section_search_extent(search_extent_results: Dict) -> str:
    """真實搜尋延伸資料附錄：依語意群列出 Google 關聯關鍵字 + 月均搜尋量 + 競爭度。"""
    if not search_extent_results:
        return ""
    lines = [
        "## 附錄：真實搜尋延伸資料（Google Ads Keyword Planner）",
        "",
        "依各語意群核心關鍵字向 Google 查得的真實關聯關鍵字與月均搜尋量，"
        "為 §7 延伸建議的接地證據。",
        "",
    ]
    for cid in sorted(search_extent_results.keys()):
        data = search_extent_results[cid]
        label = data.get("label") or f"群 {cid + 1}"
        seeds = "、".join(data.get("seeds", []))
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"*種子關鍵字：{seeds}*")
        lines.append("")
        lines.append("| 關聯關鍵字 | 月均搜尋量 | 競爭度 |")
        lines.append("| :--- | ---: | :--- |")
        for idea in data.get("ideas", [])[:20]:
            vol = idea.get("avg_monthly_searches")
            vol_s = f"{vol:,}" if isinstance(vol, int) else "-"
            comp = idea.get("competition") or "-"
            lines.append(f"| {idea.get('text', '')} | {vol_s} | {comp} |")
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
             llm_model: str,
             search_extent_results: Dict = None) -> str:
    """組裝最終 Markdown 報告。

    Args:
        synthesis_parts: {summary, search_intent_analysis, recommendations, expansion}
        nlp_results:     {tfidf, clusters}
        llm_results:     {search_intents, qualitative}
        search_extent_results: {cluster_id: {label, seeds, ideas}}（真實搜尋延伸，可空）
    """
    search_extent_results = search_extent_results or {}
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
        " 數值層結合 TF-IDF 關鍵字、Vertex AI 語意分群、關聯規則探勘與 Cloud NL 實體／情感，"
        "再由 LLM 解讀數值結果並延伸質性洞察。\n\n"
        + synthesis_parts.get("summary", "")
    )

    # § 2 TF-IDF（程式直接生成）
    sections.append(_section_tfidf(nlp_results.get("tfidf", {})))

    # § 3 語意分群（程式直接生成）
    sections.append(_section_clusters(nlp_results.get("clusters", {})))

    # § 3.1 主題關聯規則（程式直接生成；無資料則略過）
    assoc_section = _section_assoc(nlp_results.get("assoc", {}))
    if assoc_section:
        sections.append(assoc_section)

    # § 3.2 關鍵實體與情感（程式直接生成；未啟用 Cloud NL 則略過）
    entities_section = _section_entities(nlp_results.get("entities", {}))
    if entities_section:
        sections.append(entities_section)

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

    # § 7 延伸關鍵字與內容缺口（Synthesis LLM）；有 search-extent 真實資料則標註已接地
    if synthesis_parts.get("expansion"):
        grounded = bool(search_extent_results)
        intro = (
            "本節結合本批內容分析與 **Google 真實搜尋資料**（Ads Keyword Planner），"
            "找出與同一群受眾相關、但這批 dataset 未涵蓋、有真實搜尋需求的延伸關鍵字與內容缺口。"
            if grounded else
            "本節跳出本批內容，推論與同一群受眾相關、但這批 dataset 未直接涵蓋、"
            "可延伸經營的關鍵字、內容缺口與周邊主題。"
        )
        sections.append(
            "## 7. 延伸關鍵字與內容缺口（dataset 之外的相關機會）\n\n"
            + intro + "\n\n"
            + synthesis_parts.get("expansion", "")
        )

    # 真實搜尋延伸資料附錄（有 search-extent 結果時）
    se_section = _section_search_extent(search_extent_results)
    if se_section:
        sections.append(se_section)

    # 附錄（程式直接生成）
    sections.append(
        _section_appendix(
            nlp_results.get("tfidf", {}).get("per_article", []),
            llm_results.get("search_intents", []),
        )
    )

    return "\n\n---\n\n".join(sections)
