# InsightOut — 內容策略分析平台

> insight + inside-out：把藏在內容裡的洞察翻出來，讓行銷與編輯看見「市場已驗證的有效方向」。

InsightOut 幫授權的行銷／編輯團隊回答一個問題：**「我應該做什麼內容，才會有效果？」**
你提供一批受歡迎的內容網址，平台自動爬取、分析，產出一份含「用戶搜尋情境」的洞察報告。

- **正式網址**：https://insightout.annexix.cc （部署中，目前可用 Cloud Run 網址）
- **使用對象**：白名單授權的分析小組（非開放註冊）

---

## 核心方法論

1. **市場已驗證的基準線**：在 Google 前三頁、IG／YouTube 高擴散、論壇高互動都有人做且表現好的主題，代表需求真實存在。
2. **差異化切點**：在上述內容中，找出閱聽眾在意、但現有內容沒說好的落差（gap）—— 這就是最有競爭力的切入點。

報告最在意的問題：**用戶會用什麼關鍵字組合（搜尋情境）找到這類內容。**

---

## 使用說明（Web UI）

### 1. 登入
用授權的 Google 帳號登入。未在白名單者會看到「等待授權」頁，需管理員批准。

### 2. 建立專案
在「我的專案」建立一個 Project，填入名稱與說明。
進入 Project 後，到右側**專案設定**填入你的 **LLM API Key**（Gemini 或 Claude，自備）。
> Project 支援多人協作：Owner 可邀請 Editor（可分析）與 Viewer（只看報告）。

### 3. 爬取資料集
在 Project 頁的「① 爬取資料集」貼入網址清單（每行一個，最多 100），命名後按「開始爬取」。
系統在後端非同步爬取，完成後成為一份**資料集文件**，可檢視每篇的成功／略過／失敗。

### 4. 一鍵分析
資料集爬完後，按「🚀 將此資料集送去分析」。
分析引擎跑雙路（TF-IDF + Vertex AI 語意分群 + LLM 質化），產出 Markdown 報告。

### 5. 看報告 / 下載
報告含：共同關鍵字、語意主題分類、**用戶搜尋情境分析**、質化洞察、可操作建議。
可線上瀏覽，也可下載 `.md`。

---

## 從 Colab / 外部工具使用

系統管理員可在 `/admin/api-keys` 核發 API 金鑰（含 `crawl` / `analyse` 權限）。
持金鑰即可從 Colab 直接呼叫爬蟲與分析服務 —— 詳見 `crawler-service/crawler_README.md`。

---

## 系統架構

四個獨立的 Google Cloud Run 服務，以 HTTP API + `X-API-Key` 溝通：

| 服務 | 角色 |
|------|------|
| **content-analyser** | Web UI + 控制平面（認證、Project、金鑰管理、監控）|
| **content-crawler** | 爬取引擎（Chrome + Selenium，同步與非同步 API）|
| **analysis-pipeline** | 分析引擎（TF-IDF + Vertex AI Embedding + LLM → Markdown）|
| **search-extent** | §7 真實搜尋接地（Google Ads Keyword Planner 關鍵字擴展）|

資料儲存於 Firestore，金鑰由 Google Secret Manager 管理。

---

## 文件導覽（三支柱）

文件以三支柱組織，各支柱為「索引中樞」，連結並說明其下各文件的用途：

| 支柱 | 文件 | 內容 |
|------|------|------|
| 🟦 **產品** | [`product_guideline.md`](product_guideline.md) | 完整產品規格（架構、API、Firestore schema、角色、設計決策）|
| 🟩 **開發** | [`DEVELOPMENT.md`](DEVELOPMENT.md) | 開發中樞：索引 development_plan / CODE_REVIEW / OPTIMIZATION / SECURITY_INCIDENTS / FRONTEND_HANDOFF / changelog |
| 🟧 **維護** | [`MAINTENANCE.md`](MAINTENANCE.md) | 運維中樞：技術棧、維護腳本、金鑰、初始化/崩潰重建/回滾；索引 DEPLOY_CHECKLIST |

規範（獨立、被支柱引用）：

| 文件 | 用途 |
|------|------|
| [`CLAUDE.md`](CLAUDE.md) | 開發治理規範（口令制、Git 流程、安全）+ 架構附錄 |
| [`deploy.md`](deploy.md) | ⛔ 部署鐵則（部署前必須取得明確口令）|
| [`crawler-service/crawler_README.md`](crawler-service/crawler_README.md) | 爬蟲服務 API 說明 |

---

## 本地開發

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # 填入實際值
FLASK_DEBUG=1 python main.py
```

部署需取得明確部署口令後執行 `bash deploy.sh`（見 `deploy.md`）。
