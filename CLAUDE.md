# CLAUDE.md — Content Analyser CN（InsightOut）

**Version:** 3.1  
**Scope:** 本專案的全部開發、測試、部署規範。適用於 Claude Code 與所有開發協作者。  
**Primary Rule:** 安全、可回溯、先計畫後執行。Safety, traceability, and plan-before-action come first.

---

## ⛔ 部署鐵則（最高優先，不可弱化）

**任何 build / deploy 操作，在收到使用者明確的部署口令前，絕對禁止執行。**
（`gcloud builds submit`、`gcloud run deploy`、`bash deploy.sh`、`firebase deploy`，含背景/重新/單一服務部署。）

- 完成程式碼後必須**停下來問「是否部署？」**，等口令，才動。
- **核准開發 ≠ 核准部署。** 寫完程式碼不代表可以部署。
- 不可先啟動部署再補口令（先斬後奏）。
- 有效部署口令：`核准部署` / `核准部署：正式` / `核准部署：測試` / `核准部署：單一服務`。
- 完整鐵則見 `deploy.md`。違反屬嚴重操作錯誤。

---

## 0. 核心原則

1. **先理解，再修改**  
   在任何程式碼變更前，先閱讀本文件、現有架構、相關模組與錯誤脈絡。不得只改表面錯誤。

2. **先計畫，後執行**  
   任何會改動檔案、資料庫、部署設定、Git 歷史或雲端資源的行為，都必須先提出計畫並等待批准。

3. **最小變更**  
   每次只處理明確範圍內的問題。避免順手重構、擴張功能或改動未授權模組。

4. **高度模組化**  
   系統拆分為清楚邊界的模組與服務。三個 Cloud Run 服務（`content-analyser`、`content-crawler`、`analysis-pipeline`）之間**只透過 HTTP API 溝通**，不得共用程式碼或直接呼叫對方的內部函式。

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
| `核准部署：正式` | 部署三個 Cloud Run 服務至生產環境 |
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
python3 -m py_compile app/*.py main.py

# 爬蟲服務
python3 -m py_compile crawler-service/app.py crawler-service/crawler.py

# 分析服務
python3 -m py_compile analysis-service/*.py

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
python3 -m py_compile app/*.py main.py && \
python3 -m py_compile crawler-service/app.py crawler-service/crawler.py && \
python3 -m py_compile analysis-service/*.py && \
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

### 6.3 部署三個服務（完整）

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
  --set-env-vars "CRAWLER_SERVICE_URL=$CRAWLER_URL,ANALYSIS_SERVICE_URL=$ANALYSIS_URL" \
  --set-secrets "GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,SECRET_KEY=FLASK_SECRET_KEY:latest,GENAI_API_KEY=GENAI_API_KEY:latest,CRAWLER_API_KEY=CRAWLER_API_KEY:latest,ANALYSIS_API_KEY=ANALYSIS_API_KEY:latest"

# （重新部署分析服務時，務必加 GOOGLE_CLOUD_PROJECT 供 Vertex AI 語意分群）
# gcloud run deploy analysis-pipeline ... --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID" --set-secrets "ANALYSIS_API_KEY=ANALYSIS_API_KEY:latest"
```

> ⚠️ 提醒：`ANALYSIS_URL` 需先取得（`gcloud run services describe analysis-pipeline --region $REGION --format 'value(status.url)'`）。content-analyser 必須同時注入 `CRAWLER_SERVICE_URL` 與 `ANALYSIS_SERVICE_URL`，否則分析功能會斷線。

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


# 附錄 A：專案架構

## 概覽

**給康泰用的內容策略分析平台。**

使用者自行蒐集內容（爬蟲 / Chrome MCP / 直接貼上），送入分析引擎，產出含「用戶搜尋情境分析」的 Markdown 洞察報告。採用**三個獨立的 Google Cloud Run 服務**，以 HTTP API 溝通。

完整產品規格見 `product_guideline.md`，本附錄為快速索引。

- **GitHub**: https://github.com/hsnuhow/Content-Analyser-CN
- **管理員**: 由 `system/config.admin_email`（Firestore）決定，不寫死在程式碼

## 三服務架構圖

```
CLIENT：Web UI / Google Colab / Claude Cowork
            │ OAuth（Web）或 X-API-Key（外部工具）
            ▼
┌────────────────────────────────────────────────┐
│  content-analyser（Cloud Run，1Gi）            │
│  控制平面 + Web UI                              │
│  認證白名單 / Project 管理 / 服務監控 / 金鑰   │
└───────┬──────────────────────────┬─────────────┘
        │ HTTP X-API-Key           │ HTTP X-API-Key
        ▼                          ▼
┌──────────────────┐    ┌──────────────────────────┐
│ content-crawler  │    │ analysis-pipeline        │
│ （4Gi，Chrome）  │    │ （2Gi，NLP + Vertex AI） │
│ 爬取 → 純文字    │    │ 內容 → Markdown 報告     │
└──────────────────┘    └──────────────────────────┘
                                   │
                          Vertex AI Embedding（系統）
                          + 用戶 LLM Key（Gemini/Claude）
                                   │
                                   ▼
                          Firestore（任務、Project、報告）
```

## 目錄結構

```
Content-Analyser-CN/
├── main.py / requirements.txt / Dockerfile     主程式（無 Chrome）
├── deploy.sh                                    三服務部署腳本
├── setup_admin.sh.example                       管理員初始化範本
├── CLAUDE.md / product_guideline.md / development_plan.md / changelog.md
├── app/                            content-analyser（控制平面）
│   ├── __init__.py                 App Factory + OAuth + Blueprint 註冊
│   ├── routes.py                   主路由 + 白名單流程（/pending）
│   ├── project_routes.py           Project CRUD + 分析提交/查看/下載
│   ├── admin_routes.py             /admin：用戶管理、服務監控、金鑰
│   ├── services.py                 Firebase / Secret / 用戶管理函式
│   ├── crawler_client.py           爬蟲服務 health check 客戶端
│   ├── analysis_client.py          分析服務 HTTP 客戶端
│   ├── worker.py                   （Phase 0 已清空，預留）
│   ├── templates/                  Jinja2 模板
│   └── static/                     CSS / JS
├── crawler-service/                content-crawler（獨立）
│   ├── app.py / crawler.py / Dockerfile / requirements.txt / README.md
└── analysis-service/               analysis-pipeline（獨立）
    ├── app.py                      API 入口（非同步 job）
    ├── pipeline.py                 主協調器（雙路平行 → Synthesis）
    ├── nlp_path.py                 Path 1：TF-IDF + Vertex AI 分群
    ├── llm_path.py                 Path 2：搜尋意圖 + 質化分析
    ├── synthesis.py                Synthesis LLM
    ├── report.py                   Markdown 報告組裝
    ├── llm_client.py               Gemini / Claude 統一介面
    └── Dockerfile / requirements.txt
```

## 角色與權限

兩層角色，詳見 `product_guideline.md` 第 5 節。

**系統層**：System Admin（`system/config.admin_email`）/ Whitelisted User / Pending User
**Project 層**：Owner / Editor / Viewer

## LLM Key 分層

| 用途 | 服務 | Key 來源 |
|------|------|---------|
| 爬蟲 selector 輔助 | content-crawler | Secret Manager（系統，不公開）|
| 內容分析 | analysis-pipeline | 用戶自備（per-project，存 Firestore）|
| 語意向量 | analysis-pipeline | GCP Service Account（系統，Vertex AI）|

---

# 附錄 B：服務 API

完整 API 規格見 `product_guideline.md` 第 4.2 節與附錄。以下為快速索引。

## content-crawler（X-API-Key 保護）

| 端點 | 說明 |
|------|------|
| `GET /health` | 探活（無需金鑰）|
| `POST /api/scrape` | 爬取單一 URL，支援 `hard_timeout_sec` |
| `POST /api/scrape/batch` | 批次（最多 20）|

回傳：`{status: success/skipped/failed, url, title, content, length}`

## analysis-pipeline（X-API-Key 保護）

| 端點 | 說明 |
|------|------|
| `GET /health` | 探活（無需金鑰）|
| `POST /api/analyse` | 提交分析（非同步），回傳 `{job_id}` |
| `GET /api/analyse/{job_id}` | 查詢進度與結果 |

`POST /api/analyse` body：
```json
{
  "report_title": "...",
  "contents": [{"url","title","text","source_type"}],
  "llm_provider": "gemini|claude",
  "llm_model": "gemini-2.0-flash",
  "llm_api_key": "..."
}
```

## 從 Colab / 外部工具呼叫

```python
import requests

# 1. 爬取
crawled = requests.post(
    "https://content-crawler-xxx.run.app/api/scrape",
    json={"url": "https://example.com/article"},
    headers={"X-API-Key": CRAWLER_KEY}, timeout=300).json()

# 2. 分析（自帶 LLM Key）
job = requests.post(
    "https://analysis-pipeline-xxx.run.app/api/analyse",
    json={"report_title": "...", "contents": [...],
          "llm_provider": "gemini", "llm_model": "gemini-2.0-flash",
          "llm_api_key": GEMINI_KEY},
    headers={"X-API-Key": ANALYSIS_KEY}, timeout=30).json()

# 3. 輪詢
status = requests.get(
    f"https://analysis-pipeline-xxx.run.app/api/analyse/{job['job_id']}",
    headers={"X-API-Key": ANALYSIS_KEY}).json()
```

---

# 附錄 C：Firestore Schema

> 完整 schema 見 `product_guideline.md` 第 9 節。舊 `users/{email}/projects/` 已廢棄。

```
system/config
  admin_email: string             setup_admin.sh 寫入

users/{email}
  display_name / picture: string
  whitelist_status: string        "pending" | "approved" | "rejected"
  added_by / approved_at / last_login
  usage_log/{id}                  使用量（按用戶）

projects/{project_id}             頂層，多人協作
  title / description / owner: string
  members: map                    {email: "editor"|"viewer"}
  llm_config: map                 {provider, model, api_key}（Owner 設定）
  analyses/{analysis_id}
    report_title / status / progress / log
    job_id                        對應 analysis-pipeline 的 job
    n_articles / llm_provider / llm_model
    submitted_by / submitted_at / completed_at
    result_markdown: string

api_keys/{key_id}                 外部工具金鑰（Admin 管理）
  name / key_hash / permissions / is_active / call_count

# analysis-pipeline 自管（獨立）：
analysis_jobs/{job_id}            非同步任務狀態
  status / progress / log / result_markdown
```

---

# 附錄 D：環境變數

## content-analyser

| 變數 | 來源 | 說明 |
|------|------|------|
| `SECRET_KEY` | Secret Manager: FLASK_SECRET_KEY | Flask Session |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Secret Manager | OAuth |
| `GOOGLE_CLOUD_PROJECT` | Cloud Run 自動 | Firestore |
| `CRAWLER_SERVICE_URL` | deploy.sh 注入 | 爬蟲服務 URL |
| `CRAWLER_API_KEY` | Secret Manager | 呼叫爬蟲金鑰 |
| `ANALYSIS_SERVICE_URL` | deploy.sh 注入 | 分析服務 URL |
| `ANALYSIS_API_KEY` | Secret Manager | 呼叫分析金鑰 |
| `GENAI_API_KEY` | Secret Manager | 爬蟲 selector 輔助 |
| `FLASK_DEBUG` | 本地 `.env` | `1` = Dev 自動登入 |

## content-crawler

| 變數 | 說明 |
|------|------|
| `CRAWLER_API_KEY` | API 驗證金鑰 |
| `GENAI_API_KEY` | Gemini（selector 輔助）|
| `CHROME_BIN` / `CHROMEDRIVER_PATH` | Dockerfile 固定 |

## analysis-pipeline

| 變數 | 說明 |
|------|------|
| `ANALYSIS_API_KEY` | API 驗證金鑰 |
| `GOOGLE_CLOUD_PROJECT` | Vertex AI Embedding + Firestore |

## Secret Manager 操作

```bash
openssl rand -hex 32                              # 產生金鑰
echo -n "value" | gcloud secrets create NAME --data-file=-       # 首次
echo -n "value" | gcloud secrets versions add NAME --data-file=- # 更新
gcloud secrets list --format="table(name)"        # 確認（不讀值）
```

必要 secrets：`FLASK_SECRET_KEY`、`GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`CRAWLER_API_KEY`、`ANALYSIS_API_KEY`、`GENAI_API_KEY`

---

# 附錄 E：常用指令

## 本地開發

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # 填入實際值
FLASK_DEBUG=1 python main.py
```

## 語法檢查（全部三服務）

```bash
python3 -m py_compile app/*.py main.py && \
python3 -m py_compile crawler-service/app.py crawler-service/crawler.py && \
python3 -m py_compile analysis-service/*.py && \
bash -n deploy.sh && echo "✅ 全部通過"
```

## 首次部署

```bash
gcloud config set project YOUR_PROJECT_ID
# 確認所有 Secret Manager secrets 已建立
bash deploy.sh                       # 部署三個服務
cp setup_admin.sh.example setup_admin.sh   # 填入 admin email
bash setup_admin.sh                  # 設定管理員
# 將 Web URL + /callback 加入 OAuth 授權重新導向 URI
```

## Cloud Run 查詢

```bash
gcloud run services list --platform managed --region asia-east1
gcloud run services logs read content-analyser  --region asia-east1 --limit 50
gcloud run services logs read content-crawler   --region asia-east1 --limit 50
gcloud run services logs read analysis-pipeline --region asia-east1 --limit 50
```
