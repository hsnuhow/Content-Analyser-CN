# Changelog

## 2026-06-10 21:30:00 (爬蟲拆分為獨立 API 服務 + 對齊 Colab v3.8)
- **Refactor (Architecture)**:
    - **目的**: 將爬蟲改為一個完全獨立、透過 API 操作的 Cloud Run 服務，並以金鑰保護存取。
    - **解決方式**:
        1.  **新增獨立服務 `crawler-service/`**: 自帶 `app.py`(Flask API)、`crawler.py`、`requirements.txt`、`Dockerfile`、`README.md`。
        2.  **API 與金鑰保護**: `POST /api/scrape`（同步單篇）需帶 `X-API-Key`，以 `hmac.compare_digest` 比對環境變數 `CRAWLER_API_KEY`（來自 Secret Manager）；未設定金鑰時一律回 401。另有 `GET /health` 供探活。
        3.  **主程式改用 HTTP 呼叫**: 新增 `app/crawler_client.py`，`app/worker.py` 改呼叫 `scrape_via_api`，移除內嵌爬蟲與 `CURRENT_CRAWLER_INSTANCE`。
        4.  **移除主程式內嵌爬蟲**: 刪除 `app/crawler.py`，並自主程式 `requirements.txt` 移除 `selenium / undetected-chromedriver / selenium-stealth / google-generativeai`；主程式 `Dockerfile` 移除 Chrome 安裝。
    - **修改的程式函式/檔案**: 新增 `crawler-service/{app.py,crawler.py}`、`app/crawler_client.py`；`analysis_pipeline` in `app/worker.py`；`force_kill_crawler` in `app/admin_routes.py`；`Dockerfile`、`deploy.sh`、`requirements.txt`。
- **Feature (Crawler，對齊 Colab v3.8)**:
    - **目的**: 嚴格保留已驗證的 Colab 無頭瀏覽器爬法，只在必要處對齊套件與作法。
    - **解決方式**:
        1.  **UC 初始化修正**: `_init_driver` 統一使用 undetected-chromedriver，移除 Selenium 4 已不支援的 `desired_capabilities`（避免 TypeError），改用 `options.page_load_strategy="eager"`；移除舊的 Nix/標準 Selenium + selenium-stealth 混合分支。
        2.  **OneTrust 同意處理**: `_clear_overlays_and_click_cta` 優先呼叫 `OneTrust.AllowAll()` JS API，失敗才點 `#onetrust-accept-btn-handler` 按鈕。
        3.  **抽取防護**: 新增 `_remove_cmp_containers`，於 `_extract_main_text` 評分前移除 OneTrust/Fides/通用 CMP 容器，避免 cookie 說明被誤判為主文。
        4.  **LLM 套件遷移**: `_ask_gemini_selector` 改用新的 `google-genai`（`genai.Client` + `client.models.generate_content`，`gemini-2.0-flash`，失敗回退 `1.5-flash`）。
    - **保留加值**: `_is_listing_page`、`_scroll_and_wait_for_full_load`、噪音預過濾、`_looks_like_listing_block`、多維度評分與置信度計算等不改核心爬法的功能均保留。
    - **修改的程式函式**: `_init_driver`, `_clear_overlays_and_click_cta`, `_remove_cmp_containers`, `_extract_main_text`, `_ask_gemini_selector`, `configure_genai` in `crawler-service/crawler.py`。

## 2024-05-16
- **Fix**: 修正 `TemplateNotFound` 錯誤，重構專案結構 (`app` package)。
- **Fix**: 修正 Blueprint 註冊與全域變數存取錯誤。

## 2024-05-20
- **Feature**: 專案初始化 (Flask, Bootstrap, `.env`, `requirements.txt`)。

## 2025-12-02
- **Feature**: 實作 Headless Crawler (Selenium, Anti-detection)。
- **Feature**: 實作 Gemini LLM 輔助分析 (Selector prediction)。
- **Feature**: 實作專案制資料持久化 (Firestore: `users/{email}/projects`).
- **Feature**: 實作 Docx 報表匯出 (`python-docx`).
- **Refactor**: 導入 OAuth 身份驗證，移除舊密碼驗證。
- **Deployment**: 建立 Cloud Run 部署配置 (`Dockerfile`, `deploy.sh`) 與 Secret Manager 整合。
- **Fix**: 修復 Firebase Preview 環境登入問題 (Dev Mode Backdoor)。

## 2025-12-05
- **Feature (Crawler)**: 
    - **Log 系統即時化**: 爬蟲內部狀態 (`_log`) 即時回傳至 Firestore，前端可見詳細進度。
    - **Fides/OneTrust 支援**: 移植 Colab 版的高階遮罩處理邏輯 (API 呼叫 + 點擊)。
    - **列表區塊過濾**: 新增 `_looks_like_listing_block` 與評分過濾，避免抓取延伸閱讀。
    - **Gemini 多重比較**: 實作 `_ask_gemini_selector` 回傳多組建議，並與啟發式結果進行評分 PK。
- **UX**: 新增狀態列、停止按鈕、優化頁面佈局。

## 2025-12-05 (Current)
- **Fix (Stability)**: 
    - **問題**: Cloud Run 上發生 `invalid session id` (Chrome Crash)，研判為記憶體不足 (OOM) 與併發衝突。
    - **對策**: 實作 **全局任務鎖 (Global Lock)**，強制同一時間僅允許一個爬蟲任務執行，以時間換取空間與穩定性。
    - **對策**: 強化 `crawler.close()` 資源釋放邏輯。

## 2025-12-09
- **Fix (Crawler)**: 
    - **目的**: 解決 Marie Claire 網站爬取錯誤，避免抓取到「延伸閱讀」區塊。
    - **解決方式**:
        1.  **新增噪音預過濾**: 從 Colab 版本移植了關鍵的噪音過濾邏輯，在內容分析前移除包含 "related", "recommend", "popular" 等關鍵字的元素。
        2.  **強化候選區塊篩選**: 將原本只檢查文字長度的規則，升級為「文字長度 > 300 或段落數 >= 3」，使篩選更精準。
        3.  **確認列表區塊過濾**: 確保在評分前會過濾 `_looks_like_listing_block` 的區塊。
    - **修改的程式函式**: `_extract_main_text` in `app/crawler.py`。
- **Refactor (Architecture)**:
    - **目的**: 完成 Firestore 整合，移除廢棄的記憶體任務儲存。
    - **解決方式**: 確認 `app.config['TASKS']` 未被任何程式邏輯使用後，將其自 `app/__init__.py` 中移除，確認所有任務管理均已由 Firestore 處理。
    - **修改的程式函式**: `create_app` in `app/__init__.py`。
- **Feature (Crawler)**:
    - **目的**: 升級爬蟲評分系統，提高內容抽取的準確性。
    - **解決方式**:
        1.  **移植進階評分邏輯**: 從 Colab 版本的 `_advanced_score_node` 移植了多維度評分系統，綜合考量文本長度、段落品質、連結密度、DOM 深度、視覺權重和中文密度。
        2.  **引入置信度計算**: 新增 `_calculate_confidence` 函式，根據最佳與次佳分數的差距、絕對分數和結構特徵來計算啟發式分析的可信度。
        3.  **重構主文抽取流程**: 修改 `_extract_main_text`，使其採用新的評分與置信度流程，並根據置信度決定是否請求 Gemini LLM 輔助。
    - **修改的程式函式**: `_extract_main_text`, `_calculate_node_score`, `_calculate_confidence`, `_calculate_visual_weight`, `_calculate_dom_depth`, `_calculate_paragraph_quality` in `app/crawler.py`。
- **Fix (Crawler)**:
    - **問題**: 升級評分系統時，因 `replace` 操作失誤，意外移除了 `_looks_like_listing_block` 函式，導致 `AttributeError`。
    - **解決方式**: 重新將 `_looks_like_listing_block` 函式加回 `HeadlessCrawler` class 中。
    - **修改的程式函式**: `_looks_like_listing_block` in `app/crawler.py`。
- **Fix (Crawler)**:
    - **問題**: 爬蟲會錯誤地將文章列表 (`articleList`) 辨識為主文。
    - **解決方式**:
        1.  **強化列表過濾**: 在 `_looks_like_listing_block` 中增加 `'articlelist', 'storylist', 'postlist'` 等關鍵字，以更準確地識別列表區塊。
        2.  **調整評分權重**: 在 `_calculate_node_score` 中，降低「文字長度」的權重 (0.3 -> 0.2)，並提高「連結密度」的懲罰權重 (0.15 -> 0.25)，使其不易被充滿連結的長列表誤導。
    - **修改的程式函式**: `_looks_like_listing_block`, `_calculate_node_score` in `app/crawler.py`。
- **Refactor (Crawler)**:
    - **目的**: 根本性解決「無限滾動列表頁」與「單篇文章頁」的邏輯混淆問題。
    - **解決方式**:
        1.  **引入頁面類型分析**: 新增 `_is_listing_page` 函式，在滾動頁面前，透過檢查多重 `<article>` 標籤等結構特徵，預先判斷頁面是否為列表頁。
        2.  **實現條件執行**: 重構 `scrape` 主函式。若判斷為列表頁，則立即停止處理並回報；若為單篇文章頁，才執行新的 `_scroll_and_wait_for_full_load` 函式以確保內容完整加載。
        3.  **強化除錯日誌**: 在新的判斷與滾動流程中加入詳細的日誌，方便追蹤決策過程。
    - **修改的程式函式**: `scrape`, `_is_listing_page`, `_scroll_and_wait_for_full_load` in `app/crawler.py`。
- **Fix (Crawler)**:
    - **目的**: 修正 `_init_driver` 中的 Python 字典語法錯誤。
    - **解決方式**: 將錯誤的雙大括號 `{{}}` 修正為標準字典語法 `{}`，並補上缺少的右括號 `)`。
    - **修改的程式函式**: `_init_driver` in `app/crawler.py`。

## 2025-12-09 (UC Migration)
- **Feature (Crawler)**:
    - **目的**: 遷移至 `undetected-chromedriver` 以提升 Cloud Run 上的反偵測能力。
    - **解決方式**:
        1.  **Dockerfile 更新**: 增加 `chromedriver` 的自動下載與安裝步驟，確保版本與 `google-chrome-stable` 匹配。
        2.  **Crawler 重構**: 修改 `app/crawler.py`，導入 `undetected_chromedriver`，並移除舊的 Selenium WebDriver 初始化邏輯。特別注意 `headless=new` 與 `version_main` 的設定。
    - **修改的程式函式**: `_init_driver` in `app/crawler.py`, `Dockerfile`.

## 2025-12-11 (Development Environment Fixes)
- **Fix (Dev Environment)**:
    - **目的**: 解決在 Nix 開發環境中 `undetected-chromedriver` 報 `Status code 127` 錯誤的問題。
    - **解決方式**: 
        1.  **實作混合驅動策略**: 修改 `app/crawler.py`，在偵測到 Nix 環境時自動降級為標準 Selenium + `selenium-stealth`，僅在生產環境使用 UC。
        2.  **相依套件更新**: 在 `.idx/dev.nix` 與 `Dockerfile` 中補全 Chrome 運行所需的 Linux 系統函式庫 (`libglib`, `libnss3` 等)。
        3.  **語法修正**: 修正 `.idx/dev.nix` 中的 Nix 列表語法錯誤。
    - **修改的程式函式**: `_init_driver` in `app/crawler.py`, `.idx/dev.nix`.

## 2026-03-06 (Security & Cleanup)
- **Chore (Git)**: 
    - **目的**: 修正因包含金鑰導致 GitHub Push 失敗的問題。
    - **解決方式**: 
        1.  將 `.env` 與 `setup_secret.sh` 加入 `.gitignore`。
        2.  從 Git 索引中移除敏感檔案。
        3.  重建 Git 歷史（force push 單一乾淨 commit）以徹底清除過去 Commit 中的金鑰紀錄。✅

## 2026-06-12 (Phase 2 + Phase 3 + Phase 4)
- **Feature (Phase 2 - analysis-pipeline)**:
    - **目的**: 建立全新的獨立分析引擎，實現雙路平行 + Synthesis 架構。
    - **解決方式**:
        1.  **新增 `analysis-service/`**: 獨立 Cloud Run 服務。
        2.  **Path 1（nlp_path.py）**: TF-IDF（jieba + scikit-learn）+ Vertex AI text-multilingual-embedding-002 語意向量 + KMeans 分群。
        3.  **Path 2（llm_path.py）**: 逐批搜尋意圖萃取 + 跨文章六面向質化分析。
        4.  **Synthesis（synthesis.py）**: 整合兩路輸出，生成摘要、搜尋情境分析、可操作建議。
        5.  **報告組裝（report.py）**: TF-IDF 表格與語意群組由程式生成，LLM 負責詮釋章節。
        6.  **LLM 抽象層（llm_client.py）**: 統一 Gemini / Claude 呼叫介面。
        7.  **非同步 API**: `POST /api/analyse` 回傳 job_id，`GET /api/analyse/{job_id}` 輪詢，任務狀態存 Firestore `analysis_jobs/{job_id}`。
    - **修改的程式函式**: 新增 `run_analysis`(pipeline.py), `run`(nlp_path/llm_path/synthesis), `assemble`(report.py), `LLMClient`(llm_client.py)。

- **Feature (Phase 3 - 控制平面)**:
    - **目的**: 將 content-analyser 重構為完整控制平面 + Project 協作 Web UI。
    - **解決方式**:
        1.  **白名單流程**: `ensure_user()` 首次登入建立 pending 用戶；callback 判斷狀態；`/pending` 頁面。
        2.  **Project 管理（project_routes.py）**: 建立/設定/成員管理，Owner/Editor/Viewer 三級權限。
        3.  **分析任務**: 提交內容給 analysis-pipeline、進度輪詢、報告檢視（marked.js 渲染）、下載 .md。
        4.  **Admin 控制台**: 服務健康監控、白名單審核、Secret Manager 金鑰管理。
        5.  **新增 `analysis_client.py`**: 分析服務 HTTP 客戶端。
        6.  **新增 7 個 Jinja2 模板**：projects, project_new, project_detail, analysis_detail, pending, admin_users, 重寫 admin_dashboard。
    - **修改的程式函式**: 新增 `ensure_user`, `approve_user`, `list_all_users`(services.py)；全部路由 in `project_routes.py`、`admin_routes.py`。

- **Chore (Phase 4 - 整合收尾)**:
    - **目的**: 修正白名單漏洞、更新文件、補齊環境變數範本。
    - **解決方式**:
        1.  **修正白名單 session 漏洞**: `login_required` 在 session 缺少 whitelist_status 時從 Firestore 補查，避免舊 session 繞過審核。
        2.  **更新 `CLAUDE.md` 至 v3.0**: 附錄 A–E 改為三服務架構、新 Firestore schema、新環境變數。
        3.  **新增 `.env.example`**: 本地開發環境變數範本。
    - **修改的程式函式**: `login_required` in `app/routes.py`, `app/project_routes.py`。

## 2026-06-12 (Phase 0 + Phase 1)
- **Chore (Phase 0 - 清理地基)**:
    - **目的**: 移除所有架構錯誤的舊設計，為新架構打好基礎。
    - **解決方式**:
        1.  移除 `CRAWLER_LOCK`（全域鎖在微服務架構無意義）與 `analysis_pipeline()`（主程式不再協調爬蟲）。
        2.  移除 hardcode `ADMIN_EMAIL`，改為 `get_admin_email()` 從 Firestore `system/config` 讀取。
        3.  刪除 `app/export_utils.py`（輸出改為 Markdown）。
        4.  精簡 `app/crawler_client.py` 為 health check only。
        5.  `requirements.txt` 移除 `beautifulsoup4`、`lxml`、`python-docx`。
        6.  修正 `devserver.sh` shebang 與 PORT 預設值。
        7.  新增 `setup_admin.sh.example` 與 `app/services.py` 的 `get_admin_email()`。
        8.  `/submit_task`、`/task_status`、`/stop_task`、`/download_project` 改為 503 stub。
    - **修改的程式函式**: `analysis_pipeline`, `CRAWLER_LOCK` in `app/worker.py`（移除）；`get_admin_email` in `app/services.py`（新增）；`admin_required` in `app/admin_routes.py`；全部路由 in `app/routes.py`。

- **Feature (Phase 1 - 爬蟲補強，對齊 Colab v3.8)**:
    - **目的**: 補強 `content-crawler` 的穩健性，對齊已驗證的 Colab v3.8 實作。
    - **解決方式**:
        1.  **新增 `UnsupportedSiteError`**: 不支援的網站（如 Dcard）直接拋出，呼叫端視為 `status=skipped`。
        2.  **Dcard 跳過**: `scrape()` 開頭偵測 `dcard.tw`，直接回傳 skipped（需登入，改用 Chrome MCP）。
        3.  **新增 `_open()` 重試邏輯**: 最多 2 次重試，含逾時偵測與 `window.stop()` 重置（對齊 Colab）。
        4.  **每頁硬性時限**: `scrape()` 加入 `hard_timeout_sec=60` 參數，在載入、遮罩、滾動後各做 deadline 檢查。
        5.  **新增 `_apply_meta_fallback()`**: 主文 < 200 字時補入 `og:description` / `meta[name=description]`（對齊 Colab）。
        6.  **`crawler-service/app.py` 版本升級為 1.2.0**：`/api/scrape` 支援呼叫端自訂 `hard_timeout_sec`。
    - **修改的程式函式**: `scrape`, `_open`, `_apply_meta_fallback`, `UnsupportedSiteError` in `crawler-service/crawler.py`；`_run_scrape`, `/api/scrape` in `crawler-service/app.py`。

## 2026-06-12 (Crawler Microservice)
- **Refactor (Architecture)**:
    - **目的**: 將爬蟲從主程式內嵌架構，拆分為完全獨立的 Cloud Run 微服務（`content-crawler`），使其可被任何外部系統（Colab、Claude Cowork 等）呼叫。
    - **解決方式**:
        1.  **新增 `crawler-service/`**: 獨立 Flask API 服務，包含 `app.py`（API 入口）、`crawler.py`（爬蟲核心）、`Dockerfile`（含 Chrome 安裝）、`requirements.txt`。
        2.  **API 端點**: `GET /health`（探活）、`POST /api/scrape`（單一 URL）、`POST /api/scrape/batch`（批次，最多 20 個 URL）。所有 `/api` 端點以 `X-API-Key` 保護（`hmac.compare_digest` 防 timing attack）。
        3.  **新增 `app/crawler_client.py`**: 主程式 HTTP 客戶端，提供 `scrape_via_api()` 與 `scrape_batch_via_api()` 兩個函式。
        4.  **更新 `app/worker.py`**: 改用 `crawler_client` 透過 HTTP 呼叫，不再內嵌 Chrome。
        5.  **精簡主程式 `Dockerfile`**: 移除所有 Chrome / ChromeDriver 安裝，映像大幅縮小。
        6.  **更新 `deploy.sh`**: 先部署 `content-crawler`，取得其 URL 後注入主程式並部署 `content-analyser`。
    - **修改的程式函式**: `analysis_pipeline` in `app/worker.py`；新增 `scrape_via_api`, `scrape_batch_via_api` in `app/crawler_client.py`；新增 `scrape`, `scrape_batch`, `health`, `_run_scrape` in `crawler-service/app.py`。
- **Refactor (Crawler Core)**:
    - **目的**: 對齊已驗證的 Colab v3.8 爬法，修正累積的技術債。
    - **解決方式**:
        1.  移除 Selenium 4 已廢棄的 `desired_capabilities`，改用 `options.page_load_strategy = "eager"`。
        2.  OneTrust 遮罩優先呼叫 `OneTrust.AllowAll()` JS API（失敗才點按鈕）。
        3.  主文抽取前移除整個 OneTrust / Fides CMP 容器（避免 cookie 說明被誤判為主文）。
        4.  LLM 選擇器輔助從舊版 `google-generativeai` 遷移至新版 `google-genai`（`genai.Client` 寫法）。
        5.  統一使用 `undetected-chromedriver`，移除 Nix 環境 selenium-stealth 混合分支（爬蟲服務僅在 Cloud Run 執行）。
    - **修改的程式函式**: `_init_driver`, `configure_genai`, `_ask_gemini_selector`, `_remove_cmp_containers`, `_clear_overlays_and_click_cta` in `crawler-service/crawler.py`。
- **Docs**:
    - **目的**: 建立標準技術文件，供 Claude Code 協作使用。
    - **解決方式**: 新增 `CLAUDE.md`，記錄架構、API 規格、Firestore schema、環境變數清單、常用指令與開發規範。
