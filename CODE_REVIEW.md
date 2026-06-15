# CODE_REVIEW.md — InsightOut 全專案程式碼審查

> 📍 屬【開發支柱】，由 [DEVELOPMENT.md](DEVELOPMENT.md) 索引。
> 日期：2026-06-13　|　方法：4 模組並行審查（content-analyser / analysis-service / crawler-service / 前端模板）
> 維度：Security / Correctness / Performance / Maintainability / UIUX
> ⚠️ 行號為審查當時估計，實際修正前請核對。本文件為**審查記錄**。
> 部分項目已於後續處理（如 C2 LLM JSON 解析穩健化、C3 marked+DOMPurify XSS、M2 email 大小寫、爬蟲 driver 重用/續批等），
> 落地記錄見 [changelog.md](changelog.md)；尚未處理者持續追蹤。

## 各模組 Verdict

| 模組 | Verdict | 最大風險 |
|------|---------|---------|
| content-analyser 後端 | Request Changes | list_projects 全集合掃描、email 大小寫 |
| analysis-service | Request Changes | LLM 回應 JSON 解析不穩健 |
| crawler-service | Request Changes | SSRF、scroll 逾時整篇失敗 |
| 前端模板 UIUX | Request Changes | marked.js XSS、報告表格無樣式 |

---

## 🔴 Critical（優先處理）

| # | 模組 | 位置 | 問題 | 建議 |
|---|------|------|------|------|
| C1 | crawler | crawler-service/app.py（爬取入口）| **SSRF**：使用者可提交任意 URL，無內網/保留 IP 過濾，Cloud Run 環境可能打到 metadata endpoint（169.254.169.254）| 加 URL 驗證：拒絕私有/保留 IP 段、非 http(s) scheme、metadata 主機 |
| C2 | analysis | analysis-service/pipeline.py（LLM 回應解析）| **LLM 回應 JSON 解析脆弱**：假設乾淨 JSON，LLM 回 markdown 包裹/多餘文字就解析失敗 → 整個 job failed（分析失敗最常見原因）| 用 regex 抽取 `{...}` + try/except fallback，解析失敗給降級結果而非整個失敗 |
| C3 | 前端 | analysis_detail.html:60-64 | **marked.js XSS**：LLM 產生的 Markdown 直接 `innerHTML`，無 sanitize（提示注入可產生惡意 script）| 加 `DOMPurify.sanitize()` 或 marked sanitize 選項 |
| C4 | 前端 | analysis_detail.html:56-64 | **報告表格無 Bootstrap 樣式**：報告（核心產出）含 Markdown 表格（TF-IDF 關鍵字等），marked 渲染後無框線、難讀 | 渲染後對 `#report-content table` 加 `class="table table-striped"` 或注入 CSS |
| C5 | crawler | crawler-service/crawler.py（scroll 段）| **scroll 逾時整篇失敗**：清單頁（Cosmopolitan）持續載入超過 hard_timeout 300s 被砍，**整篇失敗且不保留已抓內容** | 達 timeout/max_scrolls 時保留已抓內容回傳，而非整篇 fail |

> 註：content-analyser agent 把 `list_projects` 全集合掃描標為 critical，但實際是**可擴展性**問題（小規模可用、隨資料成長劣化），歸入 🟡 中（P1）。

---

## 🟡 中（建議修）

| # | 模組 | 位置 | 問題 | 建議 |
|---|------|------|------|------|
| M1 | content-analyser | project_routes.py:106-118 | `list_projects` 用 `projects.stream()` 全集合掃描查 member，隨專案數劣化、且無分頁 | 為 members 建反向索引或改資料模型（member subcollection / array-contains 查詢）|
| M2 | content-analyser | services.py:106-135 | `ensure_user` admin 比對用 `.lower()` 但 doc id 用原始 email，Google 回大小寫不同會建重複 user doc | 統一用 `.lower()` 當 doc id |
| M3 | content-analyser | __init__.py:18 | `SECRET_KEY` 未設時用隨機值 → 每次重啟所有 session 失效 | 確認生產環境 `SECRET_KEY` 必設且穩定（已在 Secret Manager，需確認注入）|
| M4 | content-analyser | project_routes.py `_sync_crawling_dataset` | job 回 error 狀態仍寫一次 Firestore（更新 updated_at），多餘寫入 | error 時 early return 不寫 |
| M5 | analysis | app.py:111 | `llm_model` 從請求帶入無白名單，可傳任意字串 | 加模型名白名單 |
| M6 | analysis | pipeline.py（分群）| 單篇/少量文章時 `n_clusters > n_samples` 會拋例外 | 加 contents 數量邊界檢查 |
| M7 | analysis | llm_client.py / embeddings.py | LLM / Vertex AI 呼叫無 timeout，job 可能無限阻塞 | 加 timeout + 有限 retry |
| M8 | analysis | pipeline.py | job 狀態機中途例外可能停在 running | try/finally 確保最終狀態寫入 |
| M9 | crawler | crawl_job.py | 批次中 driver crash 後續篇是否全失敗需確認 | 確認單篇 crash 後能重建 driver 繼續批次 |
| M10 | crawler | crawler.py（RSC regex）| `["p","text"]` regex 對巢狀引號/跳脫可能解析錯 | 更穩健的 JSON 邊界解析 |
| M11 | 前端 | analysis_detail.html:40-43 | completed/failed 用 `location.reload()` 整頁刷新，閃爍、捲動位置遺失 | 局部更新 DOM |
| M12 | 前端 | analysis_detail.html:67-72 | 分析失敗無「重新分析」入口（使用者剛遇到的痛點）| 失敗區塊加重試按鈕 |
| M13 | 前端 | analysis_detail.html / dataset_detail.html | 輪詢無逾時上限，卡 running 時無限輪詢且無提示 | 加最大輪詢時間 + 逾時提示 |
| M14 | 前端 | project_detail.html:60-82 | 資料集列窄螢幕擠壓（名稱+badge+下載鈕同列）| 窄螢幕堆疊或下載鈕收 dropdown |
| M15 | content-analyser | services.py + admin_routes.py:147 | 明文 API 金鑰經 session flash 顯示，client-side session 會進 cookie | 確認金鑰顯示一次後從 session 移除 |

---

## 🟢 低（可選 / backlog）

| # | 模組 | 問題 | 建議 |
|---|------|------|------|
| L1 | content-analyser | worker.py 疑似舊同步爬蟲 dead code | 確認後移除 |
| L2 | 全部 | gemini 模型名預設值散落多處 | 抽 `DEFAULT_LLM_MODEL` 常數 |
| L3 | content-analyser | crawler_client / analysis_client 結構幾乎相同 | 抽共用 base |
| L4 | analysis | pipeline.py 函式偏長（Path1/2/Synthesis）| 拆小函式 |
| L5 | crawler | crawler.py 單一 class 多職責 | 拆模組 |
| L6 | 各服務 | API 金鑰驗證無 rate limiting | 加速率限制 |
| L7 | 前端 | 進度條無 ARIA、emoji 圖示無 aria-label | 加無障礙屬性 |
| L8 | content-analyser | list_all_users 全集合無分頁 | admin 用戶量大時加分頁 |

---

## 建議的修正優先序（若要動工）

**第一批（Critical，安全 + 核心產出）**
1. C3 marked.js XSS（前端，安全）
2. C4 報告表格樣式（前端，核心產出可讀性）— 與 C3 同檔可一起
3. C2 LLM JSON 解析穩健性（analysis，分析穩定性）
4. C1 SSRF URL 過濾（crawler，安全）
5. C5 scroll 逾時保留部分結果（crawler，順便解 Cosmopolitan）

**第二批（中，穩定性 + 體驗）**
- M7 LLM/Vertex timeout、M6 單篇分群邊界、M8 狀態機 finally（analysis 穩定性）
- M11/M12/M13 前端輪詢體驗（局部更新、重試、逾時）
- M2 email 大小寫、M3 SECRET_KEY 確認、M4 多餘寫入

**第三批（可擴展性 + 整理）**
- M1 list_projects 資料模型、L1 dead code、L2 模型常數

## 正面觀察 ✅
- OAuth/白名單/三級 RBAC 設計清晰、守衛已統一（auth_guards）
- 16 個 POST form CSRF 全覆蓋
- API 金鑰 SHA-256 hash + 常數時間比對
- 爬蟲對齊已驗證 Colab 邏輯、CMP/scroll/driver 重用處理完整
- 雙路分析方法論清晰
- 前端空狀態與導航設計良好、navbar 響應式正確
