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
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Callable, Optional

from llm_client import LLMClient
from prompt_safety import INJECTION_GUARD, wrap_untrusted
import json_utils

INTENT_BATCH_SIZE = 5   # 每批處理幾篇文章
INTENT_MAX_WORKERS = 4  # 意圖萃取批次的並行上限（避免觸發 LLM rate limit）


def _parse_llm_json(raw: str) -> str:
    """穩健清理 LLM 回傳並抽取最外層 JSON 物件字串（共用 json_utils）。"""
    return json_utils.clean_json_str(raw)
MAX_TEXT_FOR_INTENT = 2000   # 逐篇意圖萃取時，每篇最多取前 N 字（標準）
MAX_TEXT_FOR_QUAL = 2500     # 跨篇質化分析時，每篇最多取前 N 字（標準）
MAX_ARTICLES_FOR_QUAL = 30   # 質化分析最多取前 N 篇（標準）

# 輸入內容量分級（B）：放寬截斷字數與篇數，讓大 context window 模型吃更多原文。
INPUT_SCALE_PRESETS = {
    "standard": {"intent_chars": 2000, "qual_chars": 2500,  "max_articles": 30},
    "large":    {"intent_chars": 4000, "qual_chars": 5000,  "max_articles": 60},
    "max":      {"intent_chars": 8000, "qual_chars": 12000, "max_articles": 100},
}


def _resolve_limits(input_scale: str) -> dict:
    return INPUT_SCALE_PRESETS.get((input_scale or "standard"), INPUT_SCALE_PRESETS["standard"])


# ──────────────────────────────────────────────────────────────────────
# 2a：逐篇搜尋意圖萃取
# ──────────────────────────────────────────────────────────────────────

def _extract_intent_batch(batch: List[Dict], llm: LLMClient,
                          intent_chars: int = MAX_TEXT_FOR_INTENT) -> List[Dict]:
    """對一批文章呼叫 LLM，萃取每篇的搜尋意圖。回傳 list of article dicts。"""
    articles_block = ""
    for i, c in enumerate(batch, 1):
        title = wrap_untrusted(c.get("title", "無標題"), "TITLE")
        text = wrap_untrusted((c.get("text") or c.get("content") or "")[:intent_chars])
        src = c.get("source_type", "未知來源")
        articles_block += f"\n---\n【文章 {i}】（來源：{src}）\n標題：{title}\n內容：{text}\n"

    prompt = f"""{INJECTION_GUARD}你是資深內容策略分析師，請分析以下 {len(batch)} 篇受歡迎的內容。

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
        raw = llm.generate(prompt, temperature=0.3, max_tokens=2048, category="search_intent")
        raw = _parse_llm_json(raw)
        data = json.loads(raw)
        return data.get("articles", [])
    except Exception as e:
        print(f"[Path 2a] 批次解析失敗（{e}），跳過此批", flush=True)
        # 標記解析失敗，讓上層能統計並回報「有幾篇沒分析到」，避免報告看似完整實則殘缺。
        return [{"index": i + 1, "search_intents": [], "_parse_failed": True}
                for i in range(len(batch))]


def run_search_intent(contents: List[Dict], llm: LLMClient,
                      log_fn: Optional[Callable] = None,
                      intent_chars: int = MAX_TEXT_FOR_INTENT,
                      should_stop: Optional[Callable[[], bool]] = None) -> List[Dict]:
    """逐批萃取每篇文章的搜尋意圖。

    回傳：[{url, title, search_intents: [{scenario, keywords, label}]}]

    各批次彼此獨立，改為並行呼叫 LLM（上限 INTENT_MAX_WORKERS），把原本逐批序列
    等待壓成 ≈ 最慢一批的時間。ex.map 保留批次順序，故最終 results 順序與 contents 一致。
    """
    starts = list(range(0, len(contents), INTENT_BATCH_SIZE))
    batches = [contents[s: s + INTENT_BATCH_SIZE] for s in starts]
    total = len(contents)

    done = {"n": 0}
    lock = threading.Lock()

    def _work(batch: List[Dict]) -> List[Dict]:
        # 合作式停止：上層逾時已放棄等待時，仍排隊中的批次直接跳過，不再燒 LLM token
        # （結果註定被丟棄）。ex.map 會把這些篇計為「未分析到」，與既有漏回處理一致。
        if should_stop and should_stop():
            return []
        res = _extract_intent_batch(batch, llm, intent_chars=intent_chars)
        if log_fn:
            with lock:
                done["n"] += len(batch)
                n = done["n"]
            try:
                log_fn(f"[Path 2a] 搜尋意圖萃取 {n} / {total}")
            except Exception:
                pass
        return res

    if batches:
        workers = min(INTENT_MAX_WORKERS, len(batches))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            batch_results_list = list(ex.map(_work, batches))
    else:
        batch_results_list = []

    results: List[Dict] = []
    failed = 0
    for batch_start, batch_results in zip(starts, batch_results_list):
        batch = contents[batch_start: batch_start + INTENT_BATCH_SIZE]
        # ⚠️ 依 LLM 回傳的 1-based index 對位，**不可用陣列位置**：LLM 不保證回傳順序＝輸入順序、
        #   也不保證筆數相符（可能重排/漏回/多回）。用位置會把 A 篇的搜尋意圖貼到 B 篇 url（張冠李戴）。
        by_index = {}
        for r in (batch_results or []):
            try:
                ix = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            if 1 <= ix <= len(batch) and ix not in by_index:
                by_index[ix] = r
        for j in range(len(batch)):
            ar = by_index.get(j + 1)
            # ar is None＝LLM 漏回這篇；_parse_failed＝整批解析失敗 → 都計為「未分析到」，避免靜默殘缺
            if ar is None or ar.get("_parse_failed"):
                failed += 1
                intents = []
            else:
                intents = ar.get("search_intents", [])
            idx = batch_start + j
            results.append({
                "url": contents[idx].get("url", ""),
                "title": contents[idx].get("title", ""),
                "search_intents": intents,
            })

    if failed:
        msg = f"[Path 2a] ⚠️ {failed}/{total} 篇意圖萃取解析失敗（該批無搜尋情境）"
        print(msg, flush=True)
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    return results


# ──────────────────────────────────────────────────────────────────────
# 2b：跨文章六面向質化分析
# ──────────────────────────────────────────────────────────────────────

def run_qualitative_analysis(contents: List[Dict], llm: LLMClient,
                             qual_chars: int = MAX_TEXT_FOR_QUAL,
                             max_articles: int = MAX_ARTICLES_FOR_QUAL,
                             should_stop: Optional[Callable[[], bool]] = None) -> str:
    """跨文章六面向質化分析（最多取前 max_articles 篇）。

    回傳：Markdown 格式的分析文字（六個 ### 段落）
    """
    analysis_set = contents[:max_articles]

    articles_block = ""
    for i, c in enumerate(analysis_set, 1):
        title = wrap_untrusted(c.get("title", "無標題"), "TITLE")
        text = wrap_untrusted((c.get("text") or c.get("content") or "")[:qual_chars])
        src = c.get("source_type", "未知")
        articles_block += f"\n---\n【文章 {i}】（來源：{src}）\n標題：{title}\n{text}\n"

    prompt = f"""{INJECTION_GUARD}你是資深內容策略分析師。以下是 {len(analysis_set)} 篇受歡迎的內容，\
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

    # 合作式停止：上層已逾時放棄等待時，不再發出這次昂貴呼叫（結果註定被丟棄）。
    if should_stop and should_stop():
        return "（質化分析逾時中止）"
    try:
        return llm.generate(prompt, temperature=0.3, max_tokens=4096, category="qualitative")
    except Exception as e:
        print(f"[Path 2b] 質化分析生成失敗：{e}", flush=True)
        return "（質化分析生成失敗，請重新分析）"


# ──────────────────────────────────────────────────────────────────────
# Path 2 主函式
# ──────────────────────────────────────────────────────────────────────

def run(contents: List[Dict], llm: LLMClient,
        log_fn: Optional[Callable] = None,
        input_scale: str = "standard",
        should_stop: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Path 2 主函式：搜尋意圖萃取 + 六面向質化分析。

    input_scale（B 輸入內容量）：standard / large / max，放寬每篇字數與篇數上限。
    should_stop：合作式停止回呼（上層逾時放棄等待時設為 True）→ 仍排隊的 LLM 呼叫跳過，
                 避免「結果註定被丟棄卻仍燒 token」。預設 None＝不改變行為。
    """
    limits = _resolve_limits(input_scale)

    def _log(msg: str):
        print(msg, flush=True)
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    # 2a 與 2b 彼此獨立，並行執行：意圖萃取（內部又並行多批）與質化分析同時進行，
    # 把兩段原本序列的等待壓成 ≈ 較慢一段的時間。
    _log(f"[Path 2a] 開始逐篇搜尋意圖萃取（{len(contents)} 篇，輸入量={input_scale}）...")
    _log(f"[Path 2b] 開始跨文章六面向質化分析（取前 {min(len(contents), limits['max_articles'])} 篇）...")

    def _intent():
        return run_search_intent(contents, llm, log_fn=log_fn,
                                 intent_chars=limits["intent_chars"],
                                 should_stop=should_stop)

    def _qual():
        return run_qualitative_analysis(contents, llm,
                                        qual_chars=limits["qual_chars"],
                                        max_articles=limits["max_articles"],
                                        should_stop=should_stop)

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_intent = ex.submit(_intent)
        f_qual = ex.submit(_qual)
        search_intents = f_intent.result()
        qualitative = f_qual.result()

    total_intents = sum(len(a["search_intents"]) for a in search_intents)
    _log(f"[Path 2a] 完成，共萃取 {total_intents} 個搜尋情境")
    _log(f"[Path 2b] 完成（{len(qualitative)} 字）")

    return {
        "search_intents": search_intents,
        "qualitative": qualitative,
    }
