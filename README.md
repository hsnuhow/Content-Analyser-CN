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

三個獨立的 Google Cloud Run 服務，以 HTTP API + `X-API-Key` 溝通：

| 服務 | 角色 |
|------|------|
| **content-analyser** | Web UI + 控制平面（認證、Project、金鑰管理、監控）|
| **content-crawler** | 爬取引擎（Chrome + Selenium，同步與非同步 API）|
| **analysis-pipeline** | 分析引擎（TF-IDF + Vertex AI Embedding + LLM → Markdown）|

資料儲存於 Firestore，金鑰由 Google Secret Manager 管理。

---

## 文件導覽

| 文件 | 用途 |
|------|------|
| `product_guideline.md` | 完整產品規格（架構、API、Firestore schema、設計決策）|
| `CLAUDE.md` | 開發治理規範 + 架構附錄 |
| `development_plan.md` | 開發計畫與分期 |
| `DEPLOY_CHECKLIST.md` | 首次部署準備清單 |
| `deploy.md` | ⛔ 部署鐵則（部署前必須取得明確口令）|
| `OPTIMIZATION.md` | 待最佳化項目 / 技術債 backlog |
| `changelog.md` | 變更記錄 |
| `crawler-service/crawler_README.md` | 爬蟲服務 API 說明 |

---

## 本地開發

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # 填入實際值
FLASK_DEBUG=1 python main.py
```

部署需取得明確部署口令後執行 `bash deploy.sh`（見 `deploy.md`）。
