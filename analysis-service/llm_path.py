# -*- coding: utf-8 -*-
"""
Path 2：LLM 直讀層

2a. 逐篇（批次）搜尋意圖萃取
    核心問題：用戶在什麼情境下、用什麼詞組組合，會找到這篇文章？

2b. 跨文章六面向質化分析
    共同訴求 / 行銷語言 / 語氣與信任機制 / 文案結構 / 主題版圖 / 受眾訊號

費用：用戶 per-project LLM Key（Gemini 或 Claude），每次分析 < $0.005
"""
import json
from typing import List, Dict, Any, Callable, Optional

from llm_client import LLMClient

INTENT_BATCH_SIZE = 5   # 每批處理幾篇文章
MAX_TEXT_FOR_INTENT = 2000   # 逐篇意圖萃取時，每篇最多取前 N 字
MAX_TEXT_FOR_QUAL = 2500     # 跨篇質化分析時，每篇最多取前 N 字
MAX_ARTICLES_FOR_QUAL = 30   # 質化分析最多取前 N 篇（避免超出 context window）


# ──────────────────────────────────────────────────────────────────────
# 2a：逐篇搜尋意圖萃取
# ──────────────────────────────────────────────────────────────────────

def _extract_intent_batch(batch: List[Dict], llm: LLMClient) -> List[Dict]:
    """對一批文章呼叫 LLM，萃取每篇的搜尋意圖。回傳 list of article dicts。"""
    articles_block = ""
    for i, c in enumerate(batch, 1):
        title = c.get("title", "無標題")
        text = (c.get("text") or c.get("content") or "")[:MAX_TEXT_FOR_INTENT]
        src = c.get("source_type", "未知來源")
        articles_block += f"\n---\n【文章 {i}】（來源：{src}）\n標題：{title}\n內容：{text}\n"

    prompt = f"""你是資深內容策略分析師，請分析以下 {len(batch)} 篇受歡迎的內容。

{articles_block}

請針對每篇文章，列出 3–4 個「用戶搜尋情境」——描述什麼樣的用戶，在什麼狀態或需求下，\
會用什麼搜尋詞組找到這篇文章。

以 JSON 格式回傳（不要加任何說明或 markdown 標記）：
{{
  "articles": [
    {{
      "index": 1,
      "search_intents": [
        {{
          "scenario": "情境描述（用戶狀態/需求）",
          "keywords": "2–4 個詞的搜尋組合",
          "label": "簡短標籤（3–5 字）"
        }}
      ]
    }}
  ]
}}"""

    try:
        raw = llm.generate(prompt, temperature=0.3, max_tokens=2048)
        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        return data.get("articles", [])
    except Exception as e:
        print(f"[Path 2a] 批次解析失敗（{e}），跳過此批", flush=True)
        return [{"index": i + 1, "search_intents": []} for i in range(len(batch))]


def run_search_intent(contents: List[Dict], llm: LLMClient,
                      log_fn: Optional[Callable] = None) -> List[Dict]:
    """逐批萃取每篇文章的搜尋意圖。

    回傳：[{url, title, search_intents: [{scenario, keywords, label}]}]
    """
    results: List[Dict] = []

    for batch_start in range(0, len(contents), INTENT_BATCH_SIZE):
        batch = contents[batch_start: batch_start + INTENT_BATCH_SIZE]
        end = min(batch_start + INTENT_BATCH_SIZE, len(contents))
        if log_fn:
            try:
                log_fn(f"[Path 2a] 搜尋意圖萃取 {batch_start + 1}–{end} / {len(contents)}")
            except Exception:
                pass

        batch_results = _extract_intent_batch(batch, llm)

        for j in range(len(batch)):
            article_result = batch_results[j] if j < len(batch_results) else {}
            idx = batch_start + j
            results.append({
                "url": contents[idx].get("url", ""),
                "title": contents[idx].get("title", ""),
                "search_intents": article_result.get("search_intents", []),
            })

    return results


# ──────────────────────────────────────────────────────────────────────
# 2b：跨文章六面向質化分析
# ──────────────────────────────────────────────────────────────────────

def run_qualitative_analysis(contents: List[Dict], llm: LLMClient) -> str:
    """跨文章六面向質化分析（最多取前 MAX_ARTICLES_FOR_QUAL 篇）。

    回傳：Markdown 格式的分析文字（六個 ### 段落）
    """
    analysis_set = contents[:MAX_ARTICLES_FOR_QUAL]

    articles_block = ""
    for i, c in enumerate(analysis_set, 1):
        title = c.get("title", "無標題")
        text = (c.get("text") or c.get("content") or "")[:MAX_TEXT_FOR_QUAL]
        src = c.get("source_type", "未知")
        articles_block += f"\n---\n【文章 {i}】（來源：{src}）\n標題：{title}\n{text}\n"

    prompt = f"""你是資深內容策略分析師。以下是 {len(analysis_set)} 篇受歡迎的內容，\
涵蓋品牌業配、雜誌評比、KOL 口碑與真實用戶討論。

{articles_block}

請深度閱讀以上所有內容，以 Markdown 格式輸出六個面向的分析。\
每個面向使用 ### 標題，直接輸出分析內容，不要前言或後記。

### 共同訴求
這些內容在「賣」什麼感受或狀態？（超越功能，深入情感訴求）

### 關鍵語言：成分、規格如何成為行銷貨幣
哪些成分名、技術詞彙被反覆用來建立信任？它們的使用模式是什麼？

### 語氣類型與信任機制
識別 2–4 種不同的寫作語氣，說明每種語氣如何建立對應的信任關係。

### 標題與內文結構公式
受歡迎內容的標題有什麼共同公式？內文開場、展開、收尾有什麼慣用結構？

### 核心—外圍主題版圖
哪些主題是「核心」（大量內容圍繞）？哪些是「外圍延伸」？描述這個內容生態圖。

### 受眾輪廓與平台分工
目標讀者是誰？不同平台（雜誌、Dcard、IG、Threads 等）各自承擔什麼角色？"""

    return llm.generate(prompt, temperature=0.3, max_tokens=4096)


# ──────────────────────────────────────────────────────────────────────
# Path 2 主函式
# ──────────────────────────────────────────────────────────────────────

def run(contents: List[Dict], llm: LLMClient,
        log_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Path 2 主函式：搜尋意圖萃取 + 六面向質化分析。"""

    def _log(msg: str):
        print(msg, flush=True)
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    _log(f"[Path 2a] 開始逐篇搜尋意圖萃取（{len(contents)} 篇）...")
    search_intents = run_search_intent(contents, llm, log_fn=log_fn)
    total_intents = sum(len(a["search_intents"]) for a in search_intents)
    _log(f"[Path 2a] 完成，共萃取 {total_intents} 個搜尋情境")

    _log(f"[Path 2b] 開始跨文章六面向質化分析（取前 {min(len(contents), MAX_ARTICLES_FOR_QUAL)} 篇）...")
    qualitative = run_qualitative_analysis(contents, llm)
    _log(f"[Path 2b] 完成（{len(qualitative)} 字）")

    return {
        "search_intents": search_intents,
        "qualitative": qualitative,
    }
