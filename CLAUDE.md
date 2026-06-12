# CLAUDE.md — Content Analyser CN

**Version:** 2.0  
**Scope:** 本專案的全部開發、測試、部署規範。適用於 Claude Code 與所有開發協作者。  
**Primary Rule:** 安全、可回溯、先計畫後執行。Safety, traceability, and plan-before-action come first.

---

## 0. 核心原則

1. **先理解，再修改**  
   在任何程式碼變更前，先閱讀本文件、現有架構、相關模組與錯誤脈絡。不得只改表面錯誤。

2. **先計畫，後執行**  
   任何會改動檔案、資料庫、部署設定、Git 歷史或雲端資源的行為，都必須先提出計畫並等待批准。

3. **最小變更**  
   每次只處理明確範圍內的問題。避免順手重構、擴張功能或改動未授權模組。

4. **高度模組化**  
   系統拆分為清楚邊界的模組與服務。兩個 Cloud Run 服務（`content-analyser`、`content-crawler`）之間**只透過 HTTP API 溝通**，不得共用程式碼或直接呼叫對方的內部函式。

5. **API 邊界優先**  
   主程式只能透過 `app/crawler_client.py` 呼叫爬蟲服務。不得在主程式中直接 `import crawler`。

6. **文件即合約**  
   新增或修改 API、Firestore 結構、環境變數、部署流程，必須同步更新本文件與 `changelog.md`。

7. **可回溯**  
   重大變更前確認 Git 狀態，確保可以比較、還原、追蹤。

8. **保護機密**  
   不讀取、不列印、不提交 API key、`.env`、service account 或其他敏感檔案。

---

## 1. 開發觀念

### 1.1 全域分析

修改程式碼前，分析影響範圍：

- 前端模板（`app/templates/`）與 JavaScript（`app/static/js/`）
- 後端路由（`app/routes.py`、`app/admin_routes.py`）、Pipeline（`app/worker.py`）
- 爬蟲服務（`crawler-service/`）：API 入口、核心邏輯
- Firestore 資料結構（collection path、欄位、讀寫頻率）
- 環境變數與 Secret Manager 設定
- 部署腳本（`deploy.sh`、`Dockerfile`、`crawler-service/Dockerfile`）
- `changelog.md` 更新需求

回應時必須簡要說明「影響範圍」與「不會碰觸的範圍」。

### 1.2 不假設環境

除非明確說明，不得假設：

- GCP Project ID 或 Cloud Run 服務 URL
- Firebase / Firestore 已設定完成
- Secret Manager 金鑰已建立
- 本地 `.env` 比遠端設定新
- 使用者同意自動部署或推送

### 1.3 模組化與 API 邊界

所有新增功能必須遵守：

1. **單一責任**：路由、業務邏輯、Firestore 存取、Secret 載入、爬蟲呼叫，不得混在同一層。
2. **禁止跨服務直接存取**：主程式不得直接執行 Chrome、import crawler.py，只能透過 HTTP API。
3. **避免隱性耦合**：禁止依賴未文件化的路徑、Firestore collection path、magic string。
4. **可測試邊界**：重要邏輯應能在不啟動完整應用的情況下被測試。

### 1.4 API 文件化要求

新增或修改 API，必須同步更新本文件的「附錄 B：爬蟲服務 API」，至少包含：

- 端點路徑與 HTTP Method
- 用途說明
- Request / Response schema
- 驗證方式（是否需要 X-API-Key）
- 錯誤格式
- 觸及的 Firestore collections

---

## 2. 工作流程

### 2.1 開始任務前

必須先完成：

1. 閱讀 `CLAUDE.md`（本文件）
2. 閱讀 `changelog.md` 最新 5 條記錄，了解近況
3. 執行 `git status` 確認工作區狀態
4. 整理「現況理解」後，才開始提案

### 2.2 提案格式

任何檔案修改前，使用以下結構回應：

```md
## 目標
- ...

## 現況理解
- ...

## 技術棧確認
- Backend: Python 3.11 / Flask 3.x
- Runtime: Google Cloud Run
- Data store: Google Firestore
- Secrets: Google Secret Manager
- 前端: Jinja2 + Bootstrap 5

## 影響範圍
- 會修改：...
- 不會修改：...

## API 與文件影響
- API 有無變更：是/否
- 需更新文件：...

## 風險
- ...

## 實施計畫
1. ...
2. ...

## 驗證方式
- python3 -m py_compile ...
- bash -n deploy.sh

## 需要批准
請使用指定口令批准：核准改善 / 核准修正 / 核准開發 / 核准執行
```

### 2.3 有效批准口令

只有以下口令才能授權執行：

| 口令 | 適用情境 |
|------|---------|
| `核准開發` | 新增功能 |
| `核准修正` | 修正 bug |
| `核准改善` | 重構或優化 |
| `核准執行` | 一次性操作（腳本、指令）|
| `核准回復` | 回退到舊版本 |
| `核准部署` | 正式部署（等同「核准部署：正式」）|
| `核准部署：正式` | 部署兩個 Cloud Run 服務至生產環境 |
| `核准部署：單一服務` | 只部署指定的單一服務 |
| `核准推送` | git push |

**其他詞語如「可以」、「好」、「繼續」、「OK」、「試試看」均不構成授權。**

### 2.4 否決口令

收到 `禁止執行開發` 時，Claude Code 必須：

1. 立即停止所有待執行操作
2. 放棄目前提案
3. 向使用者確認已取消
4. 等待新指令

### 2.5 禁止自主延伸

完成指定任務後必須停止並回報。不得自行進入下一個功能、下一個模組或額外重構，除非使用者明確批准。

---

## 3. 安全設定

### 3.1 金鑰管理（強制）

所有金鑰、API token、OAuth secret 必須使用 **Google Secret Manager** 管理。

Claude Code 絕對不得：

- 在程式碼中 hardcode 金鑰
- 將金鑰存入 Firestore
- 在生產環境使用 `.env` 存放金鑰
- 在 log 中列印金鑰
- 將金鑰提交進 Git

本地開發允許的做法：

- 使用 `.env`（已加入 `.gitignore`），僅存放本地用的值或佔位符
- 生產與 staging 金鑰必須來自 Google Secret Manager

### 3.2 Firestore 是唯一主要資料庫

所有應用主要資料必須存放於 **Firestore**。不得引入其他資料庫，除非使用者明確指示。

### 3.3 敏感檔案保護

Claude Code 不得讀取、列印、複製或提交：

- `.env`、`.env.*`
- `*.pem`、`*.key`
- `serviceAccount*.json`、`firebase-adminsdk*.json`
- `setup_secret.sh`（已加入 `.gitignore`）
- 任何含有 API key 的設定檔

### 3.4 建議的 `.claude/settings.json`

```json
{
  "permissions": {
    "deny": [
      "Bash(gcloud secrets versions access:*)",
      "Bash(gcloud projects delete:*)",
      "Bash(gcloud iam:*)",
      "Bash(rm -rf:*)",
      "Bash(git push --force:*)",
      "Bash(git reset --hard:*)",
      "Bash(git clean -fd:*)"
    ],
    "ask": [
      "Bash(git push:*)",
      "Bash(git merge:*)",
      "Bash(bash deploy.sh:*)",
      "Bash(gcloud run deploy:*)",
      "Bash(pip install:*)"
    ],
    "allow": [
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(python3 -m py_compile:*)",
      "Bash(python3 -m pytest:*)",
      "Bash(bash -n:*)"
    ]
  }
}
```

---

## 4. 版本維護

### 4.1 Branch 模型

| Branch | 用途 |
|--------|------|
| `main` | 穩定、可部署 |
| `feature/<name>` | 功能開發 |
| `fix/<name>` | Bug 修正 |
| `chore/<name>` | 維護、文件、依賴更新 |

Claude worktree 分支（`claude/...`）是臨時分支，完成後合入 main 或捨棄。

### 4.2 Git 規範

任何程式碼變更前：

```bash
git status
git diff --stat
```

提交前執行語法檢查：

```bash
# 主程式
python3 -m py_compile app/routes.py app/worker.py app/crawler_client.py \
    app/services.py app/admin_routes.py app/export_utils.py main.py

# 爬蟲服務
python3 -m py_compile crawler-service/app.py crawler-service/crawler.py

# Shell scripts
bash -n deploy.sh
```

Commit 訊息格式（正體中文）：

```
<類型>：<一行摘要>

- 細節說明...

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

類型：`新增`、`修正`、`重構`、`補強`、`文件`、`設定`

### 4.3 本地版本優先

不得在未分析差異的情況下，以遠端版本覆蓋本地：

```bash
# 禁止未經分析直接執行
git reset --hard origin/main
```

---

## 5. 快照與備份

### 5.1 必須建立快照的時機

以下情境必須確認 Git 狀態或建立備份：

- 重大功能開發前
- Firestore 結構變更
- Secret Manager 設定變更
- 部署前
- 依賴套件升級
- Git merge/rebase

### 5.2 快照方式

```bash
# 建立 tag 作為快照
git tag -a snapshot-YYYYMMDD-描述 -m "snapshot: 說明"

# 查看現有快照
git tag -l "snapshot-*"
```

### 5.3 回退流程

若需回退：

1. 列出可用的 tag / commit
2. 比較選定版本與目前狀態
3. 識別哪些檔案/模組會改變
4. 等待 `核准回復`
5. 避免全面覆蓋，優先選擇性還原

---

## 6. 部署 SOP

### 6.0 部署前 Git 確認（強制，不得跳過）

```bash
# 確認在 main 分支
git branch --show-current
# 預期輸出：main

# 確認工作區乾淨
git status
# 預期：nothing to commit, working tree clean

# 確認最新 commits 符合預期
git log --oneline -5

# 確認 Dockerfile 或 requirements.txt 是否有改動（影響 build）
git diff HEAD~1 -- Dockerfile crawler-service/Dockerfile requirements.txt crawler-service/requirements.txt
```

### 6.1 語法驗證（強制，deploy 前執行）

```bash
python3 -m py_compile app/routes.py app/worker.py app/crawler_client.py \
    app/services.py app/admin_routes.py app/export_utils.py main.py && \
python3 -m py_compile crawler-service/app.py crawler-service/crawler.py && \
bash -n deploy.sh && \
echo "✅ 全部通過"
```

### 6.2 Secret Manager 確認

部署前確認以下 secrets 已建立：

| Secret 名稱 | 服務 | 說明 |
|------------|------|------|
| `FLASK_SECRET_KEY` | content-analyser | Flask session 加密 |
| `GOOGLE_CLIENT_ID` | content-analyser | Google OAuth |
| `GOOGLE_CLIENT_SECRET` | content-analyser | Google OAuth |
| `CRAWLER_API_KEY` | 兩個服務 | 爬蟲服務存取金鑰 |
| `GENAI_API_KEY` | 兩個服務 | Gemini API Key |

```bash
# 確認 secrets 存在（不讀取值）
gcloud secrets list --format="table(name)"
```

### 6.3 部署兩個服務（完整）

```bash
# 設定 GCP Project（首次或切換時）
gcloud config set project YOUR_PROJECT_ID

# 執行部署腳本（互動式，會先確認再部署）
bash deploy.sh
```

`deploy.sh` 流程：
1. 先部署 `content-crawler`（Chrome + Selenium）
2. 取得 `content-crawler` 的 Cloud Run URL
3. 注入 URL 後部署 `content-analyser`（輕量 Flask）

### 6.4 部署單一服務

```bash
PROJECT_ID=$(gcloud config get-value project)
REGION="asia-east1"

# 只重新部署爬蟲服務
gcloud builds submit crawler-service --tag gcr.io/$PROJECT_ID/content-crawler
gcloud run deploy content-crawler \
  --image gcr.io/$PROJECT_ID/content-crawler \
  --platform managed --region $REGION \
  --memory 4Gi --cpu 2 --timeout 300 --concurrency 4 \
  --set-secrets "CRAWLER_API_KEY=CRAWLER_API_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest"

# 只重新部署主程式（需先知道 crawler URL）
CRAWLER_URL=$(gcloud run services describe content-crawler \
  --region $REGION --format 'value(status.url)')
gcloud builds submit --tag gcr.io/$PROJECT_ID/content-analyser
gcloud run deploy content-analyser \
  --image gcr.io/$PROJECT_ID/content-analyser \
  --platform managed --region $REGION \
  --memory 1Gi --cpu 1 --timeout 300 \
  --set-env-vars "CRAWLER_SERVICE_URL=$CRAWLER_URL" \
  --set-secrets "GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,SECRET_KEY=FLASK_SECRET_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest,CRAWLER_API_KEY=CRAWLER_API_KEY:latest"
```

### 6.5 部署後驗證

```bash
# 確認服務狀態
gcloud run services list --platform managed --region asia-east1

# 確認爬蟲服務健康
CRAWLER_URL=$(gcloud run services describe content-crawler \
  --region asia-east1 --format 'value(status.url)')
curl "$CRAWLER_URL/health"
# 預期: {"status": "ok", "api_key_configured": true, ...}

# 確認主程式可登入
# 手動開啟瀏覽器至 Cloud Run URL，確認 Google OAuth 登入流程正常
```

### 6.6 部署後文件更新（推送前完成）

每次部署後，推送 GitHub 前完成：

1. 更新 `changelog.md`，新增本次部署記錄
2. 若有 API 或 Firestore 結構異動，更新本文件對應附錄
3. Commit 文件更新：

```bash
git add changelog.md CLAUDE.md
git commit -m "文件：更新部署記錄"

# 建立部署 tag
git tag -a deploy-YYYYMMDD-N -m "deploy: 簡述"

# 推送（需口令 核准推送）
git push origin main
git push origin deploy-YYYYMMDD-N
```

**不得 force push main。**

---

## 7. 雲端資源設計

### 7.1 GCP 服務邊界

| 服務 | 用途 | 備註 |
|------|------|------|
| Cloud Run | 兩個服務的運行環境 | 主程式 1Gi / 爬蟲服務 4Gi |
| Artifact Registry / GCR | Docker image 儲存 | |
| Firestore | 唯一主要資料儲存 | |
| Secret Manager | 所有金鑰 | |
| Google OAuth 2.0 | 使用者身份驗證 | |
| Gemini API | AI 輔助內容分析 | 外部 API |

Claude Code 不得主動執行：

```bash
gcloud services enable ...
gcloud projects delete ...
gcloud iam ...
gcloud secrets versions access ...
```

雲端基礎設施變更需使用者人工確認與執行。

### 7.2 Firestore 設計原則

變更 schema 前：

1. 定義 collection / document path
2. 定義所有權與存取模型
3. 定義讀寫頻率與成本風險
4. 同步更新本文件「附錄 C：Firestore Schema」

### 7.3 成本感知

新增雲端重資源操作前，預估：

- Firestore 讀寫次數
- Cloud Run 呼叫頻率（爬蟲每次啟動 Chrome 約 2–4Gi 記憶體）
- Gemini API token 使用量
- Secret Manager 存取頻率

---

## 8. 除錯協議

遇到問題時：

1. 重現或整理錯誤訊息與上下文
2. 確認錯誤是在哪一個服務（主程式 or 爬蟲服務）
3. 確認錯誤是否跨越 HTTP API 邊界
4. 查閱 Cloud Run logs：
   ```bash
   gcloud run services logs read content-analyser --region asia-east1 --limit 50
   gcloud run services logs read content-crawler  --region asia-east1 --limit 50
   ```
5. 說明可能的根本原因（排序）
6. 提出最小修正方案
7. 等待批准
8. 驗證修正後結果

**不得在未確認根本原因的情況下聲稱問題已解決。**

---

## 9. 程式碼標準

### 9.1 Python / Flask

- Blueprint 組織路由，Application Factory 模式建立 App
- Route handler 保持薄層：驗證 → 呼叫 service/client → 回傳
- Firestore 存取集中在 `services.py`（初始化）和各模組的明確呼叫點
- Secret 載入透過 `services.get_secret()` wrapper，不在業務邏輯中直接呼叫 SDK
- 驗證所有外部輸入（URL 格式、請求 body）
- 不在模組層級 global 存放可變應用狀態

### 9.2 兩服務的邊界規則

| 規則 | 原因 |
|------|------|
| 主程式不 import crawler.py | 爬蟲是獨立服務，主程式不應依賴其內部模組 |
| 主程式不安裝 Chrome / Selenium | 爬蟲容器才裝，主程式保持輕量 |
| 爬蟲服務不存取 Firestore | 只做爬取，回傳結果，不持久化 |
| 爬蟲服務不做使用者驗證 | 只驗證 X-API-Key，不知道 OAuth 使用者 |

### 9.3 安全編碼

- API Key 比對必須使用 `hmac.compare_digest`（防 timing attack）
- URL 格式驗證：必須以 `http://` 或 `https://` 開頭
- Session cookie 在生產環境設定 `Secure=True`
- 不在 log 中列印金鑰或完整 URL 參數

---

## 10. 相依管理

新增套件前：

1. 確認現有套件是否已能解決問題
2. 說明為何需要新套件
3. 確認與 Python 3.11 和現有套件的相容性
4. 確認在哪個服務的 `requirements.txt` 中新增
5. 等待批准後才執行 `pip install`

不得在未批准的情況下執行 `pip install`。

---

## 11. 回應風格

- 使用**正體中文**（繁體），程式碼中的識別符與 API 端點用英文
- 簡潔精準，風險意識強
- 不對證據薄弱的根本原因表示確定
- 不進行未授權的大範圍重寫
- 不假設未說明的環境條件

---

## 12. 邏輯衝突檢查

提案或文件定稿前，確認：

- 是否有任何地方假設爬蟲在主程式容器內執行？若有，修正。
- 是否有任何地方允許金鑰存於程式碼或 `.env` 生產環境？若有，修正。
- 是否有 API 變更但未更新附錄 B？若有，補上。
- 是否有 Firestore 結構變更但未更新附錄 C？若有，補上。
- 是否有部署步驟跳過語法檢查或 Secret 確認？若有，擋住。

---

## 13. 執行前最終檢查清單

修改任何東西前，確認：

- [ ] 已閱讀本文件與 changelog.md 近況
- [ ] 已執行 `git status` 確認工作區
- [ ] 已識別影響的模組（主程式 / 爬蟲服務 / 兩者）
- [ ] 已確認 API 邊界是否受影響
- [ ] 已確認是否需要更新 API 文件（附錄 B）
- [ ] 已確認是否需要更新 Firestore schema（附錄 C）
- [ ] 已考慮是否需要建立 snapshot（git tag）
- [ ] 已確認 Firestore 是主要資料儲存
- [ ] 已確認金鑰透過 Secret Manager 管理
- [ ] 已提出驗證步驟
- [ ] 已收到一個有效批准口令

任何項目未完成，停下來詢問。

---

---

# 附錄 A：專案架構

## 概覽

**給康泰用的內容爬蟲與 AI 分析平台。**

將文章 URL 貼入後，系統自動爬取全文並輸出 Word 報告（.docx）。採用兩個獨立的 Google Cloud Run 服務，以 HTTP API 互相溝通。

- **GitHub**: https://github.com/hsnuhow/Content-Analyser-CN
- **管理員 Email**: how.penguin@gmail.com

## 服務架構圖

```
使用者（瀏覽器）
      │ Google OAuth 2.0
      ▼
┌──────────────────────────────────────────────┐
│  content-analyser（Cloud Run，1Gi）          │
│  Flask Web UI + Firestore + OAuth            │
│                                              │
│  app/__init__.py      App Factory            │
│  app/routes.py        主路由（任務管理）     │
│  app/admin_routes.py  /admin 管理介面        │
│  app/worker.py        背景分析 Pipeline      │
│  app/crawler_client.py  爬蟲 HTTP 客戶端     │
│  app/export_utils.py  .docx 報告匯出        │
│  app/services.py      Firebase/Secret 初始化 │
│  app/templates/       Jinja2 模板            │
│  app/static/          CSS / JS              │
└──────────────────────────────────────────────┘
      │ HTTP POST /api/scrape
      │ Header: X-API-Key: CRAWLER_API_KEY
      ▼
┌──────────────────────────────────────────────┐
│  content-crawler（Cloud Run，4Gi）           │
│  獨立爬蟲微服務，Chrome + Selenium          │
│                                              │
│  crawler-service/app.py     API 入口         │
│  crawler-service/crawler.py 爬蟲核心         │
│  crawler-service/Dockerfile Chrome 安裝      │
└──────────────────────────────────────────────┘
      │ 低置信度時
      ▼
  Gemini API（google-genai）
      │
      ▼
  Firestore（任務狀態、使用者設定、爬取結果）
```

## 目錄結構

```
Content-Analyser-CN/
├── main.py                    Flask 入口
├── requirements.txt           主程式相依（無 Chrome/Selenium）
├── Dockerfile                 主程式容器（輕量，無 Chrome）
├── deploy.sh                  兩服務部署腳本
├── devserver.sh               本地開發啟動腳本
├── CLAUDE.md                  本文件
├── changelog.md               變更記錄
├── app/
│   ├── __init__.py            App Factory + OAuth 初始化
│   ├── routes.py              主路由（/、/submit_task、/task_status 等）
│   ├── admin_routes.py        /admin 路由（僅管理員）
│   ├── worker.py              背景 Pipeline（threading）
│   ├── crawler_client.py      爬蟲服務 HTTP 客戶端
│   ├── export_utils.py        .docx 報告生成
│   ├── services.py            Firebase Admin + Secret Manager 初始化
│   ├── templates/             Jinja2 HTML 模板
│   └── static/                CSS / JS
└── crawler-service/
    ├── app.py                 爬蟲 Flask API 入口
    ├── crawler.py             HeadlessCrawler 核心邏輯
    ├── requirements.txt       爬蟲服務相依（含 Chrome/Selenium）
    ├── Dockerfile             含 Chrome 安裝的容器
    └── README.md              爬蟲服務獨立說明
```

## 角色與權限

| 角色 | 識別 | 可存取路由 |
|------|------|-----------|
| Admin | `how.penguin@gmail.com` | 全部，含 `/admin` |
| User | 其他 Google 帳號 | `/`、`/profile`、任務相關路由 |

## API Key 使用順序（爬蟲任務）

1. 使用者個人 Gemini Key（Firestore `users/{email}.gemini_api_key`）
2. 系統預設 Key（`GENAI_API_KEY` 環境變數）
3. 若均無，純啟發式爬取（不呼叫 Gemini）

---

# 附錄 B：爬蟲服務 API

**服務名稱**: `content-crawler`  
**Base URL**: `CRAWLER_SERVICE_URL`（部署後由 `gcloud run services describe` 取得）  
**驗證**: 所有 `/api/*` 端點需 Header `X-API-Key: <CRAWLER_API_KEY>`

## GET /health

健康檢查，不需 API Key，供 Cloud Run 探活與外部監控。

**Response**:
```json
{
  "status": "ok",
  "service": "content-crawler",
  "version": "1.1.0",
  "chrome": "Google Chrome 122.0.6261.94",
  "api_key_configured": true
}
```

## POST /api/scrape

爬取單一 URL，同步執行。

**Request**:
```json
{
  "url": "https://example.com/article",
  "use_gemini": false,
  "gemini_api_key": "AIza..."
}
```

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `url` | string | ✅ | 必須以 http:// 或 https:// 開頭 |
| `use_gemini` | bool | 否 | 預設 false |
| `gemini_api_key` | string | 否 | 不傳則使用服務預設 Key |

**Response**:

| status | 說明 | HTTP |
|--------|------|------|
| `success` | 爬取成功 | 200 |
| `skipped` | 偵測為列表頁，略過 | 200 |
| `failed` | 爬取失敗 | 500 |

```json
// 成功
{"status": "success", "url": "...", "title": "...", "content": "...", "length": 1234}

// 略過
{"status": "skipped", "url": "...", "error": "Skipped: URL is an article list/category page."}

// 失敗
{"status": "failed", "url": "...", "error": "..."}
```

## POST /api/scrape/batch

批次爬取多個 URL，依序同步執行，最多 20 個。

**Request**:
```json
{
  "urls": ["https://...", "https://..."],
  "use_gemini": false,
  "gemini_api_key": "AIza..."
}
```

**Response**:
```json
{
  "results": [<result>, ...],
  "total": 2,
  "succeeded": 1,
  "failed": 1
}
```

## 從 Colab / 外部系統呼叫

```python
import requests

CRAWLER_URL = "https://content-crawler-xxxxx.run.app"
API_KEY = "your-crawler-api-key"

# 單一 URL
result = requests.post(
    f"{CRAWLER_URL}/api/scrape",
    json={"url": "https://example.com/article", "use_gemini": True},
    headers={"X-API-Key": API_KEY},
    timeout=300
).json()

# 批次
batch = requests.post(
    f"{CRAWLER_URL}/api/scrape/batch",
    json={"urls": ["https://...", "https://..."]},
    headers={"X-API-Key": API_KEY},
    timeout=1500
).json()
```

---

# 附錄 C：Firestore Schema

```
users/{email}
  ├── gemini_api_key: string        使用者個人 Gemini API Key
  ├── updated_at: timestamp
  └── projects/{project_id}         Auto-ID，由 Firestore 產生
        ├── status: string          "pending" | "completed" | "failed" | "cancelled"
        ├── progress: number        0–100
        ├── log: string             最新狀態訊息（供前端顯示）
        ├── report_title: string    使用者填寫的報告名稱
        ├── input_urls: [string]    使用者輸入的 URL 清單
        ├── use_gemini: bool        是否啟用 Gemini 輔助
        ├── created_at: timestamp
        ├── updated_at: timestamp
        └── pages/{auto_id}         每個 URL 的爬取結果
              ├── url: string
              ├── status: string    "success" | "failed" | "skipped"
              ├── title: string     文章標題（成功時）
              ├── content: string   爬取的主文（成功時）
              ├── length: number    主文字元數（成功時）
              ├── error: string     錯誤訊息（失敗時）
              └── crawled_at: timestamp
```

**注意**：`pages` 子集合目前沒有排序保證，匯出 .docx 時需要依 `crawled_at` 排序。

---

# 附錄 D：環境變數

## content-analyser（主程式）

| 變數 | 來源 | 說明 |
|------|------|------|
| `SECRET_KEY` | Secret Manager: `FLASK_SECRET_KEY` | Flask Session 加密金鑰 |
| `GOOGLE_CLIENT_ID` | Secret Manager | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Secret Manager | Google OAuth Client Secret |
| `CRAWLER_SERVICE_URL` | deploy.sh 自動注入 | 爬蟲服務 Cloud Run URL |
| `CRAWLER_API_KEY` | Secret Manager | 呼叫爬蟲服務的 API Key |
| `GENAI_API_KEY` | Secret Manager | 系統預設 Gemini Key |
| `FLASK_DEBUG` | 本地 `.env` 僅 | `1` = Dev 模式，自動登入 Admin |

## content-crawler（爬蟲服務）

| 變數 | 來源 | 說明 |
|------|------|------|
| `CRAWLER_API_KEY` | Secret Manager | API 驗證金鑰 |
| `GENAI_API_KEY` | Secret Manager | Gemini API Key |
| `CHROME_BIN` | Dockerfile ENV | 固定 `/usr/bin/google-chrome` |
| `CHROMEDRIVER_PATH` | Dockerfile ENV | 固定 `/usr/bin/chromedriver` |

## Secret Manager 金鑰操作

```bash
# 產生強金鑰（CRAWLER_API_KEY 用）
openssl rand -hex 32

# 建立 secret（首次）
echo -n "your-value" | gcloud secrets create CRAWLER_API_KEY --data-file=-

# 更新 secret（後續）
echo -n "new-value" | gcloud secrets versions add CRAWLER_API_KEY --data-file=-

# 確認 secrets 存在（不讀取值）
gcloud secrets list --format="table(name)"
```

---

# 附錄 E：常用指令

## 本地開發

```bash
# 建立虛擬環境（首次）
python3 -m venv .venv

# 啟動虛擬環境
source .venv/bin/activate

# 安裝主程式相依
pip install -r requirements.txt

# 啟動主程式（Dev 模式，自動登入 how.penguin@gmail.com）
FLASK_DEBUG=1 GOOGLE_CLOUD_PROJECT=your-project-id python main.py

# 語法檢查（全部）
python3 -m py_compile app/routes.py app/worker.py app/crawler_client.py \
    app/services.py app/admin_routes.py app/export_utils.py main.py \
    crawler-service/app.py crawler-service/crawler.py && \
bash -n deploy.sh && echo "✅ 全部通過"
```

## Git 操作

```bash
# 查看狀態
git status && git log --oneline -5

# 建立 feature branch
git checkout -b feature/your-feature-name

# 建立 snapshot tag
git tag -a snapshot-YYYYMMDD-描述 -m "snapshot: 說明"

# 查看所有 tags
git tag -l --sort=-version:refname | head -10
```

## Cloud Run 查詢

```bash
# 列出所有服務
gcloud run services list --platform managed --region asia-east1

# 查看服務詳情（含 URL）
gcloud run services describe content-crawler --region asia-east1 --format "value(status.url)"

# 查看 logs
gcloud run services logs read content-analyser --region asia-east1 --limit 50
gcloud run services logs read content-crawler  --region asia-east1 --limit 50
```
