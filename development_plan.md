# 開發計畫 — Content Analyser CN

**版本：** 1.0  
**建立日期：** 2026-06-12  
**狀態：** 已核准規劃，各期開發前需個別核准  
**依據：** product_guideline.md v1.3

> 本計畫為分期藍圖。每期開發開始前，需提出具體技術提案並取得口令批准（`核准開發`）。  
> 分析引擎（Phase 2）的技術細節待 Phase 1 完成後另行討論。

---

## 現況快照

| 服務 | 狀態 | 主要問題 |
|------|------|---------|
| `content-crawler` | ✅ 運作中 | 缺少重試、硬性時限、Dcard 跳過邏輯 |
| `content-analyser` | ⚠️ 架構錯誤 | Admin email 寫死、有全域鎖、爬蟲協調邏輯殘留 |
| `analysis-pipeline` | ❌ 不存在 | 全新建立 |
| Firestore schema | ❌ 舊架構 | 全部廢棄重寫 |
| 權限系統 | ❌ 不完整 | 無 Project 層級權限、無白名單機制 |

---

## 分期概覽

```
Phase 0：清理地基        ← 移除錯誤設計，不加新功能
Phase 1：爬蟲補強        ← 對齊 Colab v3.8，強化穩健性
Phase 2：分析引擎        ← 全新建立（技術細節屆時討論）
Phase 3：控制平面        ← 重構 Web UI 為完整管理介面
Phase 4：整合與收尾      ← 端到端驗證、部署腳本更新
```

---

## Phase 0：清理地基

**目標**：移除所有架構錯誤的設計。不新增功能，只做減法與修正。  
**完成標準**：程式碼語法全部通過，舊邏輯清除，安全問題解決。

### 0.1 content-analyser 清理

| 任務 | 說明 |
|------|------|
| 移除 `CRAWLER_LOCK` | `app/worker.py` 第 12 行，全域鎖在微服務架構下無意義 |
| 移除 `analysis_pipeline()` | `app/worker.py` 整個背景 Pipeline 函式，主程式不再協調爬蟲 |
| 移除 `app/export_utils.py` | 輸出改為 Markdown，DOCX 不再使用 |
| 移除 `app/crawler_client.py` 的任務呼叫邏輯 | 保留 `health_check()` 用途，移除爬蟲任務呼叫 |
| 清除 `requirements.txt` 的死亡依賴 | 移除 `beautifulsoup4`、`lxml`、`python-docx`、`selenium`（主程式不需要）|
| 移除 hardcode `ADMIN_EMAIL` | `app/routes.py`、`app/admin_routes.py` 中的 `how.penguin@gmail.com` |
| 修正 `devserver.sh` | shebang 改為 `#!/bin/bash`，加入 `PORT` 預設值（`${PORT:-8080}`）|

### 0.2 建立系統初始化腳本

| 任務 | 說明 |
|------|------|
| 建立 `setup_admin.sh` | 一次性寫入 `system/config.admin_email` 至 Firestore |
| 加入 `.gitignore` | `setup_admin.sh`（含敏感資訊，不提交）|
| 建立 `setup_admin.sh.example` | 安全的範本版本，提交進 Git 供參考 |

### 0.3 Firestore 清理說明

舊 `users/{email}/projects/` 資料**不做程式遷移**，讓它自然留在 Firestore 中孤立（不影響新系統）。可手動刪除或保留。

**新 schema 從零開始建立**，見 product_guideline.md 第 9 節。

---

## Phase 1：爬蟲補強

**目標**：讓 `content-crawler` 對齊 Colab v3.8 的穩健性設計。  
**完成標準**：每個補強項目有對應測試 URL 驗證通過。

### 1.1 補強項目（對齊 Colab v3.8）

| 任務 | 說明 | 參考位置 |
|------|------|---------|
| 加入每頁 60 秒硬性時限 | `scrape()` 加入 `hard_timeout_sec=60` 機制 | Colab `scrape_webpage()` |
| `_open()` 加入重試邏輯 | 最多 2 次重試，含逾時偵測與 `window.stop()` | Colab `_open()` |
| 頁面載入逾時調整為 25 秒 | 現在是 15 秒 | Colab `_init_driver()` |
| 加入 Dcard 跳過邏輯 | `if "dcard.tw" in url: raise UnsupportedSiteError` | Colab `scrape_webpage()` |
| 內容過短 fallback | 若抽取內容 < 200 字元，補入 `og:description` / `meta[name=description]` | Colab `_extract_main_text()` |

### 1.2 維持現有 Cloud Run 加值

以下功能是 Cloud Run 版本特有的，**不移除**：

- `_is_listing_page()`：偵測列表頁並跳過
- `/api/scrape/batch`：批次端點
- Firestore log callback（若有）

---

## Phase 2：分析引擎（analysis-pipeline）

**目標**：建立全新的 `analysis-pipeline` Cloud Run 服務。  
**狀態**：⚠️ 技術細節待 Phase 1 完成後另行討論，本計畫僅定義邊界。

### 已確認的設計決策

| 項目 | 決策 |
|------|------|
| 部署方式 | 獨立 Cloud Run 服務 |
| 任務模型 | 非同步（`POST` 回傳 `job_id`，`GET` 輪詢） |
| 輸出格式 | Markdown |
| LLM | 預設 Gemini，支援 Claude，由呼叫端提供 Key |
| 驗證 | `X-API-Key`（與 crawler 相同機制）|

### API 端點（確認）

```
GET  /health                      健康檢查，無需金鑰
POST /api/analyse                 提交分析任務，回傳 job_id
GET  /api/analyse/{job_id}        查詢進度與結果
```

### 分析步驟（待技術討論確認）

```
1. 中文斷詞（jieba）
2. TF-IDF 關鍵字萃取
3. 語意分群（TruncatedSVD，選配 BERT）
4. LLM 質性分析（Gemini / Claude）
5. Markdown 報告生成
```

> **此期開始前需另行討論**：NLP 套件選型、LLM prompt 設計、報告生成邏輯、Cloud Run 規格。

---

## Phase 3：控制平面重構

**目標**：將 `content-analyser` 重構為完整的控制平面 + Project 管理 Web UI。  
**依賴**：Phase 0 完成（舊邏輯清除）、Phase 2 API 已定義（可整合）。

### 3.1 新認證系統

| 任務 | 說明 |
|------|------|
| 從 Firestore 讀取 admin 身份 | `system/config.admin_email`，取代 hardcode |
| 白名單流程 | 用戶第一次登入 → 寫入 `users/{email}` status=pending → Admin 審核 |
| Pending 用戶頁面 | 登入後看到「等待管理員授權」提示 |
| Admin 白名單管理 UI | 查看 pending 用戶、批准/拒絕 |

### 3.2 Project 管理

| 任務 | 說明 |
|------|------|
| 建立 Project | 填入標題、說明 → 寫入 `projects/{id}`，owner = 當前用戶 |
| Project 設定頁 | 編輯標題、設定 LLM Key（僅 Owner）|
| 成員管理 | 邀請（填 email + 角色）、移除成員（僅 Owner）|
| Project 列表頁 | 列出用戶參與的所有 Project（owner 或 member）|

### 3.3 分析提交與查看

| 任務 | 說明 |
|------|------|
| 提交分析 UI | 在 Project 內貼入內容（文字），送出給 analysis-pipeline |
| 進度顯示 | 非同步輪詢 `analysis-pipeline` job 狀態，顯示進度條 |
| 分析歷史列表 | 列出 Project 內所有歷史分析，含狀態與時間 |
| 報告閱覽 | Markdown 渲染顯示報告 |
| 報告下載 | 下載 `.md` 檔案 |

### 3.4 API 金鑰管理（System Admin）

| 任務 | 說明 |
|------|------|
| 核發金鑰 | 填入名稱、說明、權限範圍（crawler / pipeline / 兩者）→ 產生金鑰、只顯示一次 |
| 金鑰清單 | 顯示所有有效金鑰（名稱、建立時間、最後使用、呼叫次數）|
| 撤銷金鑰 | 將 `is_active` 設為 false |

### 3.5 服務監控（System Admin）

| 任務 | 說明 |
|------|------|
| 服務健康狀態 | 呼叫 crawler 和 pipeline 的 `/health`，顯示版本、Chrome 狀態 |
| 使用量（按用戶）| 列出所有用戶的分析次數、最後使用時間 |

---

## Phase 4：整合與收尾

**目標**：端到端驗證整個流程，更新部署腳本，補齊文件。

### 4.1 部署腳本更新

| 任務 | 說明 |
|------|------|
| 更新 `deploy.sh` | 新增 analysis-pipeline 部署步驟（共三個服務）|
| 建立 `setup_admin.sh.example` | 範本腳本，說明首次部署流程 |
| Secret Manager 新增 | `ANALYSIS_API_KEY`（analysis-pipeline 的存取金鑰）|

### 4.2 端到端測試清單

```
□ 新用戶登入 → 看到 Pending 頁面
□ Admin 批准用戶 → 用戶可正常使用
□ 用戶建立 Project → 設定 LLM Key
□ Owner 邀請 Editor → Editor 可提交分析
□ Viewer 只能看報告，無法提交
□ 提交分析 → 看到進度 → 報告生成 → 下載 .md
□ Colab 使用 API Key → 呼叫 crawler 成功
□ Colab 使用 API Key → 呼叫 analysis-pipeline 成功
□ Admin 撤銷金鑰 → 呼叫被拒絕（401）
□ 爬蟲遭遇 Dcard URL → 正確跳過
□ 爬蟲遭遇 OneTrust 遮罩 → 正確突破
```

### 4.3 文件更新

| 任務 | 說明 |
|------|------|
| 更新 `CLAUDE.md` | 新 Firestore schema、新環境變數、新服務架構 |
| 更新 `product_guideline.md` | 確認所有實作與規格一致 |
| 更新 `changelog.md` | 各期完整記錄 |

---

## 各期依賴關係

```
Phase 0（清理地基）
    │
    ├──→ Phase 1（爬蟲補強）    ← 可與 Phase 0 平行，但最好 Phase 0 先完成
    │
    └──→ Phase 2（分析引擎）    ← 獨立建立，不依賴 Phase 1
              │
              └──→ Phase 3（控制平面）← 需要 Phase 0 完成 + Phase 2 API 定義完成
                          │
                          └──→ Phase 4（整合收尾）← 需要所有 Phase 完成
```

---

## 優化／研究項目（2026-06-14 提出）

### 待開發功能 7：資料管理 — 刪除/更名 + 孤兒清理（2026-06-14 提出）
**需求**：資料集與報告可刪除、更名；並清除孤兒資料。
**範圍**：
- 刪除資料集 `POST /<pid>/datasets/<did>/delete`（連帶清 crawl_job）；刪除報告 `POST /<pid>/analyses/<aid>/delete`（連帶清 analysis_job）。
- 更名：資料集 name / 報告 report_title。
- 孤兒清理（admin 維護）：無 dataset 引用的 crawl_jobs、無 analysis 引用的 analysis_jobs、
  指向死 job 卻卡 crawling 的 dataset。
- UI：dataset/analysis 列表加刪除/更名按鈕（CSRF + 權限：Owner/Editor）。
**狀態**：待開發。

### 待開發功能 8：強制停止爬取／分析（可取消執行階段）（2026-06-14 提出）
**需求**：使用者可中止進行中的爬取/分析；中止要能**終止執行階段、廢除資料並移除**。
**難點/設計**：爬取批次（crawl_job.py）與分析（pipeline.py）都是 server 端背景 thread，目前**無取消機制**。
- 做法：Firestore job 加 `cancel_requested` 旗標；背景 thread 在「每個 URL／每個 Path 階段」之間檢查旗標，
  命中即中止剩餘工作、把 job/dataset/analysis 標記 cancelled 並刪除其資料。
- 路由：`POST /<pid>/datasets/<did>/cancel`、`/<pid>/analyses/<aid>/cancel`。
- 注意：thread 不可硬殺；用協作式取消（檢查點）。Cloud Run 背景 thread 本就可能被 scale-down 殺掉，需穩健。
**狀態**：待開發（三者中最難，需取消 plumbing）。

### 待開發功能 9：LLM 精緻調配 — 模式/context window/溫度（2026-06-14 提出）
**需求**：讓用戶精細調 LLM：模式（model 變體）、context window、溫度。
**範圍**：
- 擴充 per-project `llm_config`：加 `temperature`、`max_output_tokens`(context)、`thinking`（Gemini 2.5 思考開關，
  呼應本次 thinking 截斷修正）等。
- UI：專案設定加溫度 slider、model 下拉、context/thinking 選項。
- 串接：analysis-pipeline 各 LLM 呼叫吃 llm_config 的這些參數（目前 temperature/max_tokens 寫死在 synthesis/llm_path）。
**狀態**：待開發。

### 研究項目 3：爬蟲研究器（Site Structure Scanner，先掃描再爬取）— ✅ 已實作（2026-06-14，site_learning.py 持久化學習選擇器 + CMS 指紋）
**構想**：對不熟悉、無模板的網站，先做一次「結構研究掃描」找出最佳主文選擇器，再正式爬取（並可回寫成新模板）。
**現況**：crawler 已有 `_ask_gemini_selector()`（置信度低時請 Gemini 建議選擇器）+ `domain_selector_cache`（同網域快取），算是雛形。
**可發展方向**：
- 獨立「research」模式：給定網域，抓 1–2 篇樣本 → Gemini 分析 DOM 摘要 → 產出建議的 SITE_TEMPLATE（indicators + selectors）→ 人工確認後寫入。
- 自我修復：某網域連續 N 次落入啟發式（未命中模板），自動觸發研究並回寫候選模板。
- 結構指紋：判斷 CMS 類型（WordPress / Next.js RSC / Hearst / fullPage.js / JSON-LD-only）後套對應抽取策略。
**效益**：降低新站台維護成本（目前是人工 curl+分析+加模板，如本次 CHANEL 實測）。
**風險/成本**：每次研究多耗 Gemini token；回寫模板需人工把關避免污染。
**狀態**：列入優化，**未核准開發**。

### 研究項目 4：YouTube 影片資料化（Tier 1 說明 + Tier 2 Gemini 口白）
**問題**：能否用 Tier 1 取得影片說明、用 Tier 2 Gemini 取得影片口白內容，組成該影片的分析資料？
**研究結論（技術可行）**：
- **Tier 1（影片說明）**：YouTube 頁面的 og:description / meta 含影片標題與部分說明；完整說明與標題可從頁面或 oEmbed
  （`youtube.com/oembed?url=...`，免 token）取得。純文字說明可爬。
- **Tier 2（影片口白/內容）**：**Gemini 2.x 原生支援 YouTube URL 影片理解**——API 以 `fileData`(fileUri=YouTube URL)
  傳入，模型可分析影片畫面+音訊，產出口白摘要/逐字稿/重點。這正好對應現有 Tier 2 的 `gemini_url_read` 概念，
  改用 video 輸入即可。
- **限制**：需公開影片；單次有長度/解析度上限（長片可能要分段或取摘要）；耗 Gemini token（影片比文字貴）；
  字幕若存在可優先抓字幕（更省）。
**建議架構**：YouTube URL → (a) oEmbed/og 取標題+說明（便宜）→ (b) 有字幕則抓字幕；無字幕才用 Gemini video 理解口白
  → 合併成 `{title, description, transcript/summary}` 當該影片資料。
**狀態**：研究完成、**未核准開發**。

### 研究項目 5：Tier 3 代理後台開關（2026-06-14 提出）
**需求**：admin 後台一個滑動開關控制 Tier 3 代理 on/off，不必每次重新部署。
**現況**：Tier 3 由 crawler 的 env `PROXY_ENABLED` 控制（改它要 `gcloud run update` 重建 revision）。
**建議架構（Firestore flag）**：
- Firestore `system/config.tier3_enabled`（bool）為單一真實來源。
- crawler `load_proxy_config()` 除了 env，再讀此 Firestore flag（crawler 已有 Firebase 連線）；
  env=0 仍可被 flag 覆寫開啟，反之亦然（決定優先序）。為省成本可加 60s 記憶體快取，不必每次 scrape 都讀。
- admin 路由 `/admin/settings` + 模板加 toggle，寫入 flag（hmac/CSRF 保護，僅 System Admin）。
**效益**：不重建即可開關 Tier 3（及未來其他爬蟲旗標如 ENABLE_YOUTUBE_TRANSCRIPT）。
**狀態**：Tier 3 已先以 env 關閉（2026-06-14）；toggle **未核准開發**。

### 研究項目 6：Cowork 蒐集資料集整合進分析（2026-06-14 提出）
**需求**：Dcard 等強反爬蟲站改用 Claude Cowork（真實瀏覽器）蒐集，產生的資料要能進分析。
**現況分析（架構已支援多資料集）**：
- 分析吃 `contents` 陣列 `[{url,title,text,source_type}]`（`submit_analysis_route` / `analyse_dataset`）。
- dataset = 已存的 items 集合；`analyse_dataset` 把「單一」dataset 的成功項目轉 contents 送分析。
- 同專案本來就能有多個 dataset（projects/{pid}/datasets/{did}），各自可一鍵分析。
- project_detail 已有手動貼 `contents_json` 入口（可直接貼 Cowork 蒐集的 JSON 做一次性分析）。
**建議整合方式（三選一或併用）**：
- **A. 手動/上傳建立資料集**：新增 `POST /<pid>/datasets/manual`（貼 JSON 或上傳檔，{url,title,text}）→
  直接建立 status=completed 的 dataset（不經爬蟲）。Cowork 資料變成一等公民資料集，照常分析。
- **B. 多資料集合併分析**：讓分析提交可勾選「多個」dataset → 合併 items 成一份 contents → 一次分析。
  例：CHANEL 分析 = 爬蟲資料集（時尚媒體）+ Cowork 資料集（Dcard）合併。最完整。
- **C. 直接貼上（已可用）**：把 Cowork 蒐集的 {url,title,text} JSON 貼進現有 contents_json textarea。
**建議**：先用 C（零開發、立即可用）；正式做 A（手動/上傳資料集）+ B（多資料集合併）。
**狀態**：研究完成、**未核准開發**。

---

## 不在此計畫範圍內（未來版本）

以下功能確認存在但不在當前計畫中：

- YouTube 分析（Gemini API 直接分析影片）→ 見上方「研究項目 4」已細化
- 報告 PDF 匯出
- Email 通知（任務完成時）
- Admin 查看所有 Project 列表

---

## 開發規範提醒

- 每期開始前提出技術提案，等待 `核准開發` 後執行
- 每個 Phase 完成後更新 `changelog.md`
- 爬蟲任何修改都以 Colab v3.8（`seo_新開發_帶ui介面爬蟲_可輸入多網址.py`）為參考基準
- 不在主程式 Dockerfile 安裝 Chrome
- 不讓主程式直接呼叫 crawler 做任務協調（只做 health check）
