# MAINTENANCE.md — 維護總文件（運維中樞）

> **支柱 3／3：維護。** 本檔是系統運維的單一入口：技術棧、維護腳本、金鑰、
> 部署方式、從零初始化、崩潰重建與回滾。內容隨系統演進更新；細節分散在被連結的文件中。
>
> 三支柱：[產品規格](product_guideline.md)｜[開發總文件](DEVELOPMENT.md)｜**維護總文件（本檔）**
> 規範：[CLAUDE.md](CLAUDE.md)（開發治理）｜[deploy.md](deploy.md)（⛔ 部署鐵則）

---

## 0. 部署待辦 / 近期狀態

### ✅ P-1 爬蟲並行安全（Cloud Tasks 佇列化）— 已部署上線（2026-06-16）
原「背景執行緒」模式多用戶並行會 OOM。已遷移為 Cloud Tasks 佇列 + 同步 worker：
content-crawler `00057-7hp`、v1.7.0、**concurrency=1**、`CRAWLER_USE_QUEUE=1`；
Cloud Tasks 佇列 `crawler-tasks`@asia-east1（RUNNING、max-concurrent-dispatches=8）；
compute SA 已綁 `roles/cloudtasks.enqueuer`。crawl/extract-images/research 三條都走佇列分塊。
- **秒回退**：`gcloud run services update content-crawler --region asia-east1 --remove-env-vars CRAWLER_USE_QUEUE`（改回背景執行緒 fallback）。
- **⏳ 唯一未做**：線上並行實爬驗證（同時觸發 2–3 個爬取、確認排隊完成且無 OOM）。靜態設定已全數確認；發起任一爬取即走佇列。

---

## 1. 系統技術棧

**產品：** InsightOut（https://insightout.annexix.cc）
**GCP Project：** `content-analyser-cn`（number `315771250032`）｜**Region：** `asia-east1`

四個獨立 Cloud Run 服務，彼此只透過 HTTP API + `X-API-Key` 溝通：

| 服務 | 角色 | 規格 | 關鍵相依 |
|------|------|------|---------|
| `content-analyser` | Web UI + 控制平面（認證/Project/金鑰/監控） | 1Gi / cpu 1 / timeout 300 | Flask 3 · Jinja2 · Bootstrap 5 · OAuth |
| `content-crawler` | 爬取引擎 → 純文字 | 4Gi / cpu 2 / timeout 300 / concurrency 1 | undetected-chromedriver · Chrome · Selenium |
| `analysis-pipeline` | 內容 → Markdown 報告（雙路） | 2Gi / cpu 1 / timeout 600 / concurrency 2 | jieba · scikit-learn · Vertex AI Embedding |
| `search-extent` | §7 真實搜尋接地（關鍵字擴展） | 1Gi | Google Ads Keyword Planner API |

- **資料庫**：Firestore（Native，asia-east1）— 唯一主要資料儲存。
- **機密**：Google Secret Manager（見 §3）。
- **執行身分**：Compute 預設 SA `315771250032-compute@developer.gserviceaccount.com`，
  具 `secretmanager.secretAccessor` / `datastore.user` / `aiplatform.user`。
- 後端 Python 3.11。架構圖與資料 schema 見 [product_guideline.md](product_guideline.md) 與 [CLAUDE.md](CLAUDE.md) 附錄 A/C。

> 目前線上 revision（隨部署變動，僅供對照）：content-analyser `00024-84b`、content-crawler `00047-whj`、
> analysis-pipeline `00014-bqd`、search-extent `00001-ghx`。

---

## 2. 維護腳本一覽（根目錄 `.sh`）

| 腳本 | 用途 | 何時用 | 注意 |
|------|------|--------|------|
| [`deploy.sh`](deploy.sh) | 完整部署 3 服務（crawler→analysis→analyser，自動串接 URL） | 首次部署、重大設定變更、災難重建 | 需部署口令；search-extent 不在內、需單獨部署 |
| [`rotate-key.sh`](rotate-key.sh) `<CRAWLER\|ANALYSIS>` | 安全輪換服務間驗證金鑰（產生→寫 Secret Manager→重部署兩端→驗證） | 要輪換 `CRAWLER_API_KEY` / `ANALYSIS_API_KEY` 時 | 維運者 gcloud 身分執行；有 1–2 分鐘空窗，離峰跑 |
| `setup_admin.sh`（由 `.example` 複製，**gitignored**） | 寫入 `system/config.admin_email` 至 Firestore | 首次部署後設定管理員 | 範本見 `setup_admin.sh.example` |
| `setup_secret.sh`（**gitignored**） | 建立/更新 Secret Manager 金鑰的本地腳本 | 首次建立金鑰、或不走後台時更新 | 含機密、不提交 |
| [`rollback.sh`](rollback.sh) `<service> [revision\|--list]` | Cloud Run 流量回滾（不重 build）：`--list` 列 revisions＋目前流量%；不指定 revision＝回前一個健康版 | 部署後發現問題要秒退、或切流量到指定 revision 時 | 維運者 gcloud 身分；只切流量不動程式碼，回滾即時生效 |
| [`devserver.sh`](devserver.sh) | 本地開發啟動（Flask debug，自動以 admin 登入） | 本地開發 | **正式環境絕不可設 FLASK_DEBUG/FLASK_ENV** |

**日常部署（最常用，安全）= image-only 單一服務**（不跑 deploy.sh）：
```bash
gcloud builds submit <dir> --tag gcr.io/content-analyser-cn/<svc>:<唯一tag>
gcloud run deploy <svc> --image gcr.io/content-analyser-cn/<svc>:<唯一tag> --region asia-east1
```
- 只換 image、保留既有 env/secrets/資源設定 → 程式碼變更的標準做法。
- ⚠️ tag 一律唯一（如 `feat-20260615`），**勿用 `:latest`**（快取陷阱）。
- 何時用 `deploy.sh` vs image-only：見 §6。

---

## 3. 金鑰與 Secret 全貌

| 金鑰 | 用途 | 來源 / 管理方式 | 存放 |
|------|------|----------------|------|
| `FLASK_SECRET_KEY` | Flask session 加密 | 系統隨機（setup 建立） | Secret Manager |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth 登入 | GCP Console | Secret Manager |
| `GENAI_API_KEY` | 爬蟲 selector 輔助（Gemini） | 維運者，**後台「Secret Manager 金鑰管理」可更新** | Secret Manager |
| `ORIGIN_VERIFY_TOKEN` | content-analyser Cloudflare 來源鎖定守衛密鑰（與 Cloudflare Transform Rule 注入的 `X-Origin-Token` 同值；缺則守衛靜默停用） | 維運者，與 Cloudflare 設定同步更新 | Secret Manager |
| `CRAWLER_API_KEY` | 服務間驗證（content-crawler） | 系統隨機（`openssl rand -hex 32`） | Secret Manager |
| `ANALYSIS_API_KEY` | 服務間驗證（analysis-pipeline） | 系統隨機 | Secret Manager |
| `SEARCH_EXTENT_API_KEY` | 服務間驗證（search-extent） | 系統隨機（該服務上線才需要） | Secret Manager |
| `PROXY_HOST`/`PORT`/`USER`/`PASS`/`PROVIDER` | Tier 3 住宅代理（Decodo）憑證 | 維運者，**後台可更新**（不存在會自動建立） | Secret Manager |
| `ADS_DEVELOPER_TOKEN` / `ADS_CLIENT_ID` / `ADS_CLIENT_SECRET` / `ADS_REFRESH_TOKEN` / `ADS_LOGIN_CUSTOMER_ID` | search-extent 呼叫 Google Ads | 維運者（待 Ads Basic 核准） | Secret Manager |
| **內容分析 LLM 金鑰** | 實際分析內容的 LLM（Gemini/Claude/OpenAI） | **用戶自備，專案 Owner 在「專案 LLM 設定」填入** | **Firestore（per-project）** |
| `iok_…`（api_keys） | 外部工具（Colab/Cowork）呼叫服務 | 管理員在 `/admin/api-keys` 產生 | Firestore（只存 hash） |

**金鑰更新規則（重要）：**
- `GENAI_API_KEY`、`PROXY_*` → `/admin` 後台「Secret Manager 金鑰管理」更新（只輸入不回顯）。
- `CRAWLER_API_KEY`、`ANALYSIS_API_KEY` → **刻意不開放後台編輯**（驗證方+呼叫方共用，改一端會中斷）；
  一律用 [`rotate-key.sh`](rotate-key.sh) 輪換。
- 機密**絕不**進 Firestore / git / console 明文（`.env` 已三重 ignore，僅本地除錯）。詳見 [CLAUDE.md](CLAUDE.md) §3.1。
- Cloud Run 的 secret 在**部署當下**綁版本 → 更新 secret 後需**重部署**該服務才生效。

---

## 4. 從零初始化（首次部署 / 全新環境）

完整逐步指引見 **[DEPLOY_CHECKLIST.md](DEPLOY_CHECKLIST.md)**（Step 1–10：建 Project、啟用 API、Firestore、OAuth、建 secret、IAM、部署、設 admin、驗證、綁網域）。摘要：

1. `gcloud config set project content-analyser-cn`；啟用 API（run / cloudbuild / artifactregistry / secretmanager / firestore / aiplatform / iamcredentials）。
2. 建 Firestore（Native, asia-east1）。
3. 設定 Google OAuth（同意畫面 + Client ID/Secret）。
4. 建立全部 Secret Manager 金鑰（見 §3；`PROXY_*` 與 `SEARCH_EXTENT_API_KEY`/`ADS_*` 視需要）。
5. 設定 Compute SA 的 IAM（secretAccessor / datastore.user / aiplatform.user）。
6. `bash deploy.sh`（3 服務）→ 視需要單獨部署 search-extent，並把 `SEARCH_EXTENT_SERVICE_URL`/`SEARCH_EXTENT_API_KEY` 注入 analyser/analysis。
7. `cp setup_admin.sh.example setup_admin.sh` → 填值 → `bash setup_admin.sh`。
8. 補正 OAuth 重新導向 URI（`https://insightout.annexix.cc/callback`）。
9. 驗證（§7）。

---

## 5. 崩潰復原 / 重啟 / 回滾

**先定位問題服務：**
```bash
gcloud run services list --platform managed --region asia-east1
gcloud run services logs read <svc> --region asia-east1 --limit 50
```

| 症狀 | 可能原因 | 處置 |
|------|---------|------|
| 服務 500 / 啟動失敗 | 程式錯誤、相依、secret 缺 | 看 log 找 traceback；回滾到前一個健康 revision（見下）|
| 登入 redirect_uri_mismatch | OAuth 重新導向 URI 未含現網址 | GCP Console 補 `…/callback` |
| `/admin` 顯示「尚未設定管理員」 | 未跑 setup_admin.sh | 重跑 setup_admin.sh |
| 分析卡 pending | analysis-pipeline 不可達 | 檢查 `ANALYSIS_SERVICE_URL` 注入、analysis 健康 |
| 語意分群失敗（報告仍出） | Vertex AI 權限 | 補 SA `aiplatform.user` |
| health `api_key_configured:false` | secret 未注入 | 檢查該服務 `--set-secrets` |
| 爬蟲/分析 401 | 服務驗證金鑰兩端不一致 | 用 [`rotate-key.sh`](rotate-key.sh) 重新對齊；勿手改單端 |
| 代理失效 | PROXY_* 缺/錯，或 Tier 3 開關 | 後台更新 PROXY_*、查 Firestore `system/config.tier3_enabled` |

**回滾方式（兩種）：**
1. **Cloud Run revision 回滾**（最快，不動程式碼）。推薦用 [`rollback.sh`](rollback.sh) 包好：
   ```bash
   bash rollback.sh <svc> --list          # 列 revisions + 目前流量%
   bash rollback.sh <svc>                  # 回前一個健康版（不指定即上一個）
   bash rollback.sh <svc> <健康revision>   # 切到指定 revision
   ```
   底層等同：`gcloud run services update-traffic <svc> --to-revisions <健康revision>=100 --region asia-east1`。
2. **回到某個部署版本**（程式碼層）：用 git 部署 tag（`deploy-YYYYMMDD-N`）：
   ```bash
   git tag -l "deploy-*"
   git checkout <deploy-tag> -- <要回退的檔>   # 或 checkout 整個 tag 後重建+部署
   ```
   每次部署都有對應 `deploy-YYYYMMDD-N` tag（見 [CLAUDE.md](CLAUDE.md) §6.6）。

**完全重建**：服務被刪除或環境重置 → 依 §4 + [DEPLOY_CHECKLIST.md](DEPLOY_CHECKLIST.md) 重跑（secret 仍在 Secret Manager 則跳過建 secret）。

---

## 6. 部署方式選擇

| 情境 | 用什麼 | 原因 |
|------|--------|------|
| 改程式碼、env/設定不變 | **image-only 單一服務**（§2） | 快、保留 env、風險低 |
| 首次建立 / 重建 / 改 env·secret·資源綁定 / 服務間 URL 串接 | **`deploy.sh`** | 只有它完整定義服務設定並串接 URL |
| 輪換服務驗證金鑰 | **`rotate-key.sh`** | 原子化更新+重部署兩端+驗證 |

⛔ 任何 build/deploy 前必須取得明確部署口令，見 [deploy.md](deploy.md)。

---

## 7. 健康檢查 / 驗證

```bash
REGION=asia-east1
for s in content-crawler analysis-pipeline search-extent; do
  URL=$(gcloud run services describe $s --region $REGION --format 'value(status.url)')
  echo "== $s =="; curl -s "$URL/health"; echo
done
# content-analyser：手動開 https://insightout.annexix.cc 確認 Google 登入正常
```
預期各服務 `{"status":"ok", "api_key_configured":true, ...}`。

---

## 8. 相關文件

| 文件 | 用途 |
|------|------|
| [DEPLOY_CHECKLIST.md](DEPLOY_CHECKLIST.md) | 首次部署逐步指引（Step 1–10）+ 費用預估 + 綁網域 |
| [deploy.md](deploy.md) | ⛔ 部署鐵則（口令制，不可弱化） |
| [CLAUDE.md](CLAUDE.md) | 開發治理規範；附錄 A 架構 / B API / C schema / D 環境變數 / E 指令 |
| [product_guideline.md](product_guideline.md) | 完整產品規格與設計決策 |
| [changelog.md](changelog.md) | 變更/部署歷史 |
