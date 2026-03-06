# Changelog

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
        3.  (Pending) 重建 Git 歷史以徹底清除過去 Commit 中的金鑰紀錄。
