# 產品規格文件 — InsightOut

**產品名稱：** InsightOut  
**正式網址：** https://insightout.annexix.cc  
**版本：** 1.5 草稿  
**撰寫日期：** 2026-06-12  
**狀態：** 規格已定，進入部署階段  
**受眾：** 產品負責人、開發協作者、Claude Code  
**GCP Project：** content-analyser-cn（技術代號）

---

## 1. 產品定位

### 1.1 這是什麼

**InsightOut** 是一個**內容策略分析平台**，服務對象為授權的行銷與編輯團隊。

> 名稱寓意：把藏在內容裡的洞察（insight）翻出來（inside-out），讓行銷與編輯看見「市場已驗證的有效方向」。

核心問題只有一個：

> **「我應該做什麼內容，才能有效果？」**

平台的答案來自兩個互補的方法論，而不是直覺或經驗。

### 1.2 不是什麼

- 不是一個開放給所有人的 SaaS 服務
- 不是一個爬蟲工具（爬蟲只是取得原料的手段之一）
- 不是一個內容管理系統（CMS）
- 不是一個自動化行銷工具

---

## 2. 核心方法論

### 方法論一：市場已驗證的基準線

**假設**：如果某個主題、切角、關鍵字，在以下渠道都有人做且表現好，代表市場需求真實存在：

- Google 搜尋結果前三頁（媒體報導、電商產品頁）
- YouTube 高播放率或高互動率影片
- IG 高擴散率貼文（高讚數、高分享、高存取）
- 論壇高互動討論（Dcard、PTT 等）

**做法**：蒐集這些已驗證的內容 → 用數值 + LLM 拆解共同模式 → 得到「做什麼方向是對的」的證據。

### 方法論二：差異化切點

**假設**：市場上表現好的內容，仍然有閱聽眾在意但沒被充分說好的角度。

**做法**：在方法論一的分析結果中，找出「高頻出現的問題或需求」與「現有內容沒有充分回應的部分」之間的落差（Gap）。這個落差就是最有競爭力的切入點。

### 方法論的關係

```
方法論一（基準線）
    ↓ 告訴你「市場在哪裡、規則是什麼」
方法論二（差異化）
    ↓ 告訴你「在這個市場裡，你可以佔哪個位置」
         ↓
   可操作的內容策略建議
```

---

## 3. 系統架構

### 3.1 設計原則

1. **計算與介面分離**：Web UI 只是一個殼，所有計算都在獨立的 Cloud Run 服務中執行
2. **服務完全獨立**：三個服務之間只透過 HTTP API 溝通，不共用程式碼
3. **資料蒐集不屬於任何服務**：使用者用任何工具蒐集原料，再送入分析引擎
4. **任何客戶端都能呼叫**：Colab、Claude Cowork、Web UI 使用相同的 API 介面

### 3.2 整體架構圖

```
╔══════════════════════════════════════════════════════════════╗
║                        CLIENT 層                            ║
║                                                             ║
║    Web UI         Google Colab       Claude Cowork          ║
║  （瀏覽器）       （Python 腳本）    （Chrome MCP）         ║
╚══════╤═══════════════════╤═══════════════════╤═════════════╝
       │                   │                   │
       │ Google OAuth       │ API Key           │ API Key
       │ + 白名單           │                   │
       ↓                   ↓                   ↓
╔══════════════════════════════════════════════════════════════╗
║                content-analyser（控制平面）                  ║
║                                                             ║
║  ┌──────────────┐  ┌───────────────┐  ┌─────────────────┐  ║
║  │  使用者管理  │  │   服務管理    │  │  API 金鑰管理   │  ║
║  │              │  │               │  │                 │  ║
║  │ OAuth 登入   │  │ 服務健康狀態  │  │ 核發 / 撤銷     │  ║
║  │ 白名單控管   │  │ 使用量監控    │  │ 權限設定        │  ║
║  │ 專案管理     │  │               │  │ 使用紀錄        │  ║
║  │ 報告瀏覽     │  │               │  │                 │  ║
║  └──────────────┘  └───────┬───────┘  └────────┬────────┘  ║
╚══════════════════════════╪══════════════════════╪══════════╝
                            │ 管理 / 監控          │ 核發金鑰
              ┌─────────────┴──────────┐           │
              ↓                        ↓           │
╔═════════════════════╗  ╔══════════════════════╗  │
║   content-crawler   ║  ║  analysis-pipeline   ║  │
║                     ║  ║                      ║  │
║  輸入：URL          ║  ║  輸入：已收集的內容  ║  │
║  輸出：純文字內容   ║  ║  輸出：分析報告      ║  │
║                     ║  ║                      ║  │
║  • Headless Chrome  ║  ║  • 中文斷詞（jieba） ║  │
║  • 媒體文章         ║  ║  • TF-IDF 關鍵字     ║  │
║  • 電商產品頁       ║  ║  • 語意分群          ║  │
║  • 公開論壇         ║  ║  • LLM 質性分析      ║  │
║                     ║  ║  • 報告生成          ║  │
╚═════════════════════╝  ╚══════════════════════╝
          ↑                          ↑
          └──────────────────────────┘
              外部工具使用 API Key 直接呼叫
```

### 3.3 資料蒐集層（不屬於任何服務）

資料蒐集是**使用者的工作**，平台不自動搜尋或發現內容。使用者手動判斷哪些內容值得分析，再用適合的工具蒐集。

```
┌────────────────────────────────────────────────────────┐
│                   資料蒐集方式                         │
│                                                        │
│  一般媒體文章、電商頁面、公開論壇                      │
│  └→ content-crawler API（自動化）                      │
│                                                        │
│  Dcard、IG、有登入牆或強反爬蟲的來源                   │
│  └→ Claude Cowork Chrome MCP（人工輔助瀏覽）           │
│                                                        │
│  YouTube 影片                                          │
│  └→ Gemini API（不爬取，直接分析影片內容）             │
│                                                        │
│  任何已有的文字內容                                    │
│  └→ 直接輸入（貼上文字）                              │
└────────────────────────────────────────────────────────┘
                          ↓
             使用者手上有「已收集的內容」
                          ↓
               送入 analysis-pipeline
```

---

## 4. 三個服務的詳細定義

### 4.1 content-crawler（爬取引擎）

| 項目 | 說明 |
|------|------|
| **職責** | 接收 URL，回傳乾淨的文字內容 |
| **輸入** | 單一 URL 或 URL 清單（最多 20 個） |
| **輸出** | `{status, url, title, content, length}`（主文在 `content` 欄位）|
| **技術** | Headless Chrome + undetected-chromedriver + Gemini 輔助選取器 |
| **不做** | 分析、儲存結果、認識使用者 |
| **呼叫方** | Colab、Claude Cowork、任何持有金鑰的工具 |
| **與 Web UI 的關係** | 無直接關係，Web UI 只負責管理它的安全性 |

**API 端點：**
- `GET /health` — 健康檢查（無需金鑰）
- `POST /api/scrape` — 爬取單一 URL
- `POST /api/scrape/batch` — 批次爬取（最多 20 個）

---

### 4.2 analysis-pipeline（分析引擎）

| 項目 | 說明 |
|------|------|
| **職責** | 接收已收集好的內容，輸出結構化分析報告 |
| **輸入** | `{report_title, contents: [{url, title, text, source_type}], llm_provider, llm_model, llm_api_key}`（每篇主文欄位 `text`，亦相容 crawler 的 `content`）|
| **輸出** | 結構化 Markdown 報告（含搜尋意圖分析）|
| **不做** | 爬取網頁、認識使用者、知道內容從哪裡來 |
| **呼叫方** | Colab、Claude Cowork、Web UI、任何持有金鑰的工具 |
| **與 Web UI 的關係** | 無直接關係，Web UI 只負責管理它的安全性 |

**API 端點：**
- `GET /health` — 健康檢查（無需金鑰）
- `POST /api/analyse` — 提交分析任務（非同步），回傳 `{job_id}`
- `GET /api/analyse/{job_id}` — 查詢進度與結果

---

#### 4.2.1 分析架構：雙路平行 + 最終整合

核心設計是**三條路同時跑，最後交給 Synthesis LLM 整合**：

```
輸入：N 篇已收集的內容
        │
        ├────────────────────────────────────────────────┐
        │                                                │
        ▼                                                ▼
【Path 1：數值分析層】                      【Path 2：LLM 直讀層】
  1a. TF-IDF 關鍵字萃取                      LLM 直接閱讀全部文章
  1b. Vertex AI Embedding + 語意分群          針對每篇萃取搜尋意圖
  → 輸出：數值報告                           → 輸出：質化洞察報告
        │                                                │
        └──────────────────┬─────────────────────────────┘
                           │
                           ▼
               【Synthesis LLM】
               接收：數值報告 + 質化洞察
               輸出：完整探勘報告
               （含跨文章搜尋情境總結）
```

兩個 Path **同時執行**（threading），完成後才進入 Synthesis。

---

#### 4.2.2 Path 1a：TF-IDF 統計層

| 技術 | 說明 | 費用 |
|------|------|------|
| `jieba` | 中文斷詞 | 本地，免費 |
| `scikit-learn TF-IDF` | 關鍵字權重計算 | 本地，免費 |

輸出：
- Top 25 關鍵字排名（詞彙 + 權重）
- 每篇文章的 Top 10 關鍵字

---

#### 4.2.3 Path 1b：語意結構層

| 技術 | 說明 | 費用 |
|------|------|------|
| `Vertex AI text-multilingual-embedding-002` | 將每篇文章轉為語意向量（768 維）| ~$0.000025/千字，每次分析 < $0.01 |
| `scikit-learn KMeans / TruncatedSVD` | 依語意向量聚類成 N 個主題群 | 本地，免費 |

- 不安裝本地 BERT 模型，純 API 呼叫
- 使用 Cloud Run Service Account（Application Default Credentials）呼叫，系統負擔費用
- 用戶不需提供任何 Key

輸出：
- N 個語意主題群（哪些文章在談同一件事）
- 每群的代表性詞彙與文章清單

---

#### 4.2.4 Path 2：LLM 直讀層（含搜尋意圖分析）

**核心問題**：用戶會因為什麼關鍵字組合（情境描述）找到這篇文章？

TF-IDF 給的是「文章裡出現的詞」，但用戶搜尋是「情境驅動的詞組」。這個差異由 LLM 來彌補。

**兩步驟：**

**Step 2a：逐篇搜尋意圖萃取**

對每篇文章呼叫 LLM：
```
這是一篇[來源類型]的內容：[文章全文]

請輸出 3–5 個「用戶搜尋情境」，格式：
- 情境描述：[用戶在什麼狀態/需求下]
  搜尋關鍵字組合：[2–4 個詞的組合]
  情境標籤：[簡短標籤]
```

**Step 2b：跨文章質化分析**

將所有文章一次送給 LLM，分析六個面向：
1. 共同訴求（市場在賣什麼感受，不是賣什麼功能）
2. 關鍵成分/規格如何成為行銷語言
3. 三種語氣類型與對應的信任機制
4. 標題與內文的結構公式（鉤子、開場、收尾）
5. 核心—外圍主題版圖（哪些話題是主角，哪些是延伸）
6. 受眾輪廓與平台分工

| 技術 | 費用 |
|------|------|
| 用戶的 Gemini / Claude Key | 用戶負擔，per-project 設定 |
| 每次分析（20 篇）約 | ~$0.003–0.005 |

---

#### 4.2.5 Synthesis LLM：最終整合

接收：Path 1（TF-IDF 數值 + 語意群組）+ Path 2（搜尋意圖 + 質化洞察）

輸出以下完整報告結構：

```markdown
# 受歡迎內容分析報告：{主題}

產生日期 | 樣本數 | 使用模型

## 1. 摘要
   資料涵蓋範圍、來源類型、方法說明

## 2. 共同關鍵字（TF-IDF）
   Top 25 關鍵字排名與權重

## 3. 語意主題分類
   N 個主題群：代表詞彙 + 所屬文章

## 4. 用戶搜尋情境分析       ← 核心新增，回答「用戶怎麼找到這類內容」
   最常見的 5–8 個搜尋情境
   每個情境：情境描述 + 代表搜尋詞組 + 覆蓋篇數

## 5. LLM 質化分析
   共同訴求 / 行銷語言 / 語氣與信任機制 /
   文案結構公式 / 主題版圖 / 受眾與平台訊號

## 6. 綜合洞察與可操作建議
   8–12 條具體建議

## 附錄：各篇搜尋意圖與關鍵字
   逐篇列出萃取的情境 + 關鍵字組合
```

---

#### 4.2.6 技術棧與費用總覽

| 元件 | 技術 | Key 來源 | 每次分析費用（20 篇）|
|------|------|---------|-------------------|
| TF-IDF | jieba + scikit-learn | 無 | $0 |
| 語意向量 | Vertex AI text-multilingual-embedding-002 | GCP Service Account（系統）| < $0.01 |
| LLM 直讀（Path 2）| Gemini 2.0 Flash / Claude | 用戶 per-project | ~$0.003 |
| Synthesis LLM | Gemini 2.0 Flash / Claude | 用戶 per-project | ~$0.003 |
| **系統月負擔**（50 次）| | | **< $0.50（台幣 16 元）**|

Cloud Run 規格（預估）：
- Memory：2Gi（scikit-learn + 中文斷詞，無本地模型）
- CPU：1
- Concurrency：2（分析任務為 CPU + I/O 混合）

---

### 4.3 content-analyser（控制平面 + Web UI）

| 項目 | 說明 |
|------|------|
| **職責** | 使用者介面 + Project 管理 + 兩個服務的安全性管理 |
| **不做** | 爬取、分析、任何計算 |
| **資料儲存** | Firestore |

**功能模組：**

**① 系統管理員功能**（僅 System Admin）
- 白名單：新增/移除授權用戶
- 服務監控：查看 content-crawler 和 analysis-pipeline 健康狀態
- API 金鑰：核發/撤銷供 Colab / Claude Cowork 使用的金鑰
- 使用量：按用戶查看分析次數與使用記錄

**② Project 管理**（Owner）
- 建立 Project、設定標題與說明
- 設定 Project 的 LLM 提供商、模型與 API Key
- 邀請成員（設定為 Editor 或 Viewer）
- 移除成員、刪除 Project

**③ 分析操作**（Owner + Editor）
- 在 Project 內提交內容進行分析
- 查看分析進度（非同步輪詢）
- 查看所有歷史分析

**④ 報告閱覽**（Owner + Editor + Viewer）
- 瀏覽分析報告（Markdown 渲染）
- 下載報告（.md 檔案）

---

## 5. 使用者、授權與 Project 權限

### 5.1 使用對象

授權的分析小組（白名單制）。目前主要為康泰納仕台灣的行銷與編輯夥伴，但不限於特定組織。

### 5.2 系統管理員設定

**管理員 email 不寫死在程式碼中**，改用一次性腳本在首次部署時寫入 Firestore：

```bash
# setup_admin.sh（執行一次即可，之後加入 .gitignore，不提交進 Git）
gcloud firestore documents create \
  "projects/-/databases/-/documents/system/config" \
  --field-mask admin_email \
  --fields "admin_email=how.penguin@gmail.com"
```

系統在執行期間從 `system/config.admin_email` 讀取管理員身份，不依賴任何程式碼常數。

### 5.3 角色層級

系統有兩個層次的角色：

**系統層（System Level）**

| 角色 | 識別方式 | 說明 |
|------|---------|------|
| **System Admin** | `system/config.admin_email` | 管理整個系統，包含白名單、API 金鑰、服務監控 |
| **Whitelisted User** | `users/{email}.whitelist_status = "approved"` | 可登入、建立 Project |
| **Pending User** | `users/{email}.whitelist_status = "pending"` | 已登入但尚未被批准，看到等待頁 |

**Project 層（Project Level）**

| 角色 | 指定方式 | 說明 |
|------|---------|------|
| **Owner** | `projects/{id}.owner` | 建立者，全權管理此 Project |
| **Editor** | `projects/{id}.members.{email} = "editor"` | 可提交分析、看報告、下載 |
| **Viewer** | `projects/{id}.members.{email} = "viewer"` | 只能看報告、下載 |

### 5.4 權限矩陣

| 功能 | System Admin | Owner | Editor | Viewer | Pending |
|------|:-----------:|:-----:|:------:|:------:|:-------:|
| 白名單管理 | ✅ | ❌ | ❌ | ❌ | ❌ |
| API 金鑰管理 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 服務監控 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 使用量監控 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 建立 Project | ✅ | ✅ | ❌ | ❌ | ❌ |
| 設定 Project LLM Key | ✅ | ✅ | ❌ | ❌ | ❌ |
| 邀請/移除成員 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 提交分析 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 查看報告 | ✅ | ✅ | ✅ | ✅ | ❌ |
| 下載報告 | ✅ | ✅ | ✅ | ✅ | ❌ |

### 5.5 LLM Key 歸屬

LLM Key 是 **per-project**，由 Owner 設定，儲存於 `projects/{id}/llm_config.api_key`。

| 使用情境 | LLM Key 來源 |
|---------|------------|
| 透過 Web UI 在 Project 內提交分析 | Project 的 LLM Key（Owner 設定）|
| 直接從 Colab 呼叫 analysis-pipeline | 呼叫時自行帶入（不存在系統裡）|

### 5.6 外部工具 API 金鑰機制

System Admin 在 Web UI 核發 API Key，供 Colab / Claude Cowork 直接呼叫服務：

```python
# Colab / Claude Cowork 使用範例

# 爬取文章
result = requests.post(
    "https://content-crawler-xxx.run.app/api/scrape",
    json={"url": "https://example.com/article"},
    headers={"X-API-Key": "YOUR_API_KEY"}
).json()

# 提交分析（自帶 LLM Key）
job = requests.post(
    "https://analysis-pipeline-xxx.run.app/api/analyse",
    json={
        "report_title": "CHANEL 初生光采 × 美白透亮",
        "llm_provider": "gemini",
        "llm_model": "gemini-2.0-flash",
        "llm_api_key": "YOUR_GEMINI_KEY",
        "contents": [
            {"url": "...", "title": "...", "text": "...", "source_type": "media"},
            {"url": "...", "title": "...", "text": "...", "source_type": "dcard"},
        ]
    },
    headers={"X-API-Key": "YOUR_API_KEY"}
).json()
# → {"job_id": "abc123"}

# 查詢進度
status = requests.get(
    f"https://analysis-pipeline-xxx.run.app/api/analyse/{job['job_id']}",
    headers={"X-API-Key": "YOUR_API_KEY"}
).json()
```

---

## 6. 報告格式

### 6.1 標準報告結構

參考已驗證的報告範本，analysis-pipeline 輸出以下結構：

```
# 受歡迎內容分析報告：{主題}

產生日期 | 樣本數 | 使用模型

## 1. 摘要
   資料涵蓋範圍、來源類型、方法備註

## 2. 共同關鍵字（TF-IDF）
   Top 25 關鍵字及權重表格

## 3. 內容主題分類（語意分群）
   N 個主題群，每群含：
   • 代表性詞彙
   • 所屬文章清單（含連結）

## 4. LLM 質性分析
   • 共同訴求（賣什麼、用什麼語言）
   • 成分/規格如何成為行銷語言
   • 語氣類型與信任機制
   • 標題與內文的結構公式
   • 主題版圖（核心—外圍結構）
   • 受眾與平台訊號

## 5. 綜合洞察與可操作建議
   8–12 條具體行動建議

## 附錄：各篇關鍵字
   逐篇列出 Top 10 關鍵字
```

### 6.2 輸出格式

| 格式 | 說明 | 狀態 |
|------|------|------|
| Markdown | 唯一輸出格式，結構清晰，可直接閱讀或轉換 | ✅ 確認 |

---

## 7. 技術棧

### 7.1 基礎設施

| 項目 | 技術 | 說明 |
|------|------|------|
| 平台 | Firebase | 整體系統放在 Firebase 生態系 |
| 資料庫 | Firestore | 唯一主要資料儲存 |
| 運算 | Google Cloud Run | 三個服務的容器運行環境 |
| AI 輔助 | Vertex AI | 視需求使用，技術棧討論時確認 |
| 金鑰管理 | Google Secret Manager | 系統層級金鑰 |

### 7.2 各服務技術

| 服務 | 語言 | 部署 | 關鍵套件 |
|------|------|------|---------|
| content-analyser | Python 3.11 / Flask | Cloud Run | Firebase Admin, Authlib |
| content-crawler | Python 3.11 / Flask | Cloud Run 4Gi | Selenium, undetected-chromedriver |
| analysis-pipeline | Python 3.11 / Flask | Cloud Run（規格待定）| jieba, scikit-learn, Gemini SDK, Claude SDK |

---

## 8. 設計決策記錄

### 8.1 已確認

| # | 問題 | 決策 | 說明 |
|---|------|------|------|
| 1 | LLM 選型 | **預設 Gemini，可選 Claude** | 系統不提供 API Key，用戶自備並存於 Firestore。用戶可自選模型 |
| 2 | 報告輸出格式 | **Markdown** | 唯一輸出格式 |
| 3 | 分析任務執行方式 | **非同步** | `POST /api/analyse` 回傳 `job_id`，前端輪詢進度 |
| 4 | 金鑰儲存位置 | **分層管理** | 系統金鑰 → Secret Manager；用戶 LLM API Key → Firestore |

### 8.2 LLM 設計細節

```
LLM Key 為 per-project，由 Project Owner 在 Project 設定頁面配置：
  • 選擇 LLM 提供商（Gemini / Claude）
  • 選擇模型（例如：gemini-2.0-flash / claude-sonnet-4-5）
  • 填入對應的 API Key → 儲存至 projects/{id}/llm_config.api_key

分析任務執行時：
  1. 從 Project 的 llm_config 讀取 LLM 提供商、模型、Key
  2. 若未設定，Owner 在提交分析前必須先完成設定
  3. Editor / Viewer 不能修改 LLM 設定

從 Colab / 外部工具直接呼叫時：
  → 呼叫端自行在 request body 帶入 llm_provider / llm_model / llm_api_key
  → 系統不提供任何 fallback LLM Key
```

### 8.3 LLM API Key 分層設計

系統中有兩個不同用途的 LLM 呼叫，金鑰來源不同：

| 用途 | 執行服務 | 金鑰來源 | 說明 |
|------|---------|---------|------|
| 爬蟲 selector 輔助 | content-crawler | Secret Manager（系統提供）| Gemini 幫助識別正文 CSS selector，基礎爬蟲功能 |
| 內容分析（報告生成）| analysis-pipeline | Firestore（用戶自備）| 核心分析功能，用戶必須自備 Key 才能使用 |

### 8.4 使用量監控

| # | 問題 | 決策 | 說明 |
|---|------|------|------|
| 5 | 使用量監控粒度 | **按用戶** | 管理員可查看每位授權用戶的分析次數、爬蟲呼叫次數等使用紀錄 |

**監控資料設計（初稿）：**
```
Firestore: users/{email}/usage_log/{log_id}
  • action: "analyse" | "crawl"
  • timestamp: datetime
  • project_id: string（分析任務）
  • url_count: number（爬蟲呼叫）
  • llm_provider: "gemini" | "claude"（分析任務）
  • status: "success" | "failed"
```

管理員在 Web UI 可查看：
- 所有用戶的使用摘要（次數、最後使用時間）
- 單一用戶的詳細使用記錄

---

## 9. Firestore Schema（全新設計）

> ⚠️ 舊有 `users/{email}/projects/` 資料結構**全部廢棄**，不做遷移。以下為全新 schema。

### 頂層 Collections 概覽

```
Firestore
├── system/                   系統設定（setup_admin.sh 寫入）
├── users/                    白名單用戶
├── projects/                 所有 Project（頂層，多人協作）
│     ├── datasets/           爬取資料集（URL → 非同步爬取結果文件）
│     └── analyses/           分析任務
├── analysis_jobs/            analysis-pipeline 自管的非同步任務狀態
├── crawl_jobs/               content-crawler 自管的非同步爬取任務狀態
└── api_keys/                 外部工具金鑰（含 hash、permissions）
```

> 實作狀態（2026-06-13）：`system`、`users`、`projects`、`datasets`、`analyses`、
> `analysis_jobs`、`crawl_jobs`、`api_keys` 均已實作。
> `users/.../usage_log`、`analyses/.../inputs` 為未來功能，尚未實作。

---

### `system/config`（單一文件）

由 `setup_admin.sh` 一次性寫入，不由程式碼管理。

```
system/config
  admin_email:  string    # 系統管理員 email
  created_at:   timestamp
```

---

### `users/{email}`

白名單用戶資料。用戶第一次登入時自動建立（status = pending）。

```
users/{email}
  email:             string
  display_name:      string
  picture:           string
  whitelist_status:  string    # "pending" | "approved" | "rejected"
  added_by:          string    # 批准者 email（admin）
  approved_at:       timestamp
  last_login:        timestamp
  created_at:        timestamp

  # ── 以下為未來功能，尚未實作 ──
  usage_log/{log_id}           # 使用量紀錄（管理員按用戶查看）
    action / project_id / analysis_id / url_count / llm_provider / status / timestamp
```

---

### `projects/{project_id}`

Project 為頂層 collection，支援多人協作。

```
projects/{project_id}
  title:        string
  description:  string
  owner:        string         # email
  created_at:   timestamp
  updated_at:   timestamp

  members:      map            # {email: "editor" | "viewer"}
                               # owner 不在 members 裡，以 owner 欄位識別

  llm_config:   map            # Owner 專屬，Editor/Viewer 不可讀寫
    provider:   string         # "gemini" | "claude"
    model:      string         # "gemini-2.0-flash" | "claude-sonnet-4-5" 等
    api_key:    string         # 明文存 Firestore（與現有 gemini_api_key 慣例一致）

  analyses/{analysis_id}       # 分析任務（對應 analysis_jobs 的 job_id）
    report_title:     string
    job_id:           string   # analysis-pipeline 的 job_id
    status:           string   # "pending" | "running" | "completed" | "failed"
    progress:         number   # 0–100
    log:              string   # 最新狀態訊息（前端輪詢顯示）
    n_articles:       number   # 輸入素材總數
    llm_provider:     string   # 實際使用的 LLM
    llm_model:        string
    submitted_by:     string   # email
    submitted_at:     timestamp
    completed_at:     timestamp
    result_markdown:  string   # 完整 Markdown 報告

    # 註：輸入內容（contents）只送往 analysis-pipeline，不落地為 inputs 子集合。
```

---

### `analysis_jobs/{job_id}`

由 analysis-pipeline 服務自管的非同步任務狀態（content-analyser 透過 job_id 輪詢）。

```
analysis_jobs/{job_id}
  job_id / status / progress / log
  report_title / n_articles / llm_provider / llm_model
  result_markdown:  string
  created_at / updated_at / completed_at
```

---

### `api_keys/{key_id}`

外部工具金鑰管理（Admin 後台 /admin/api-keys 核發）。明文只顯示一次，僅存 SHA-256 hash。
crawler 與 analysis 驗證時：先比對 Secret Manager 系統金鑰，再查此白名單（hash + is_active + permission）。

```
api_keys/{key_id}
  name / key_prefix / key_hash / permissions（["crawl","analyse"]）
  created_by / created_at / last_used_at / is_active / call_count
```

---

### `projects/{pid}/datasets/{did}`

爬取資料集（UI 輸入 URL → content-crawler 非同步爬取 → 結果文件 → 一鍵分析）。

```
projects/{pid}/datasets/{did}
  name / source_urls / crawl_job_id
  status（crawling | completed | failed）/ progress / log
  item_count / succeeded
  items: [{url, title, content, status, length, error}]   # 從 crawl_jobs 同步
  created_by / created_at / updated_at
```

---

### `crawl_jobs/{job_id}`

content-crawler 自管的非同步爬取任務狀態（content-analyser 透過 crawl_job_id 輪詢）。

```
crawl_jobs/{job_id}
  job_id / status / progress / log / total
  results: [{status, url, title, content, length}]
  succeeded / skipped / failed
  created_at / updated_at / completed_at
```

---

### `api_keys/{key_id}`

System Admin 核發的外部工具金鑰（Colab / Claude Cowork）。

```
api_keys/{key_id}
  name:         string    # 識別用途，例如「Colab - CHANEL 專案」
  description:  string
  key_hash:     string    # SHA-256 hash，不存明文
  permissions:  array     # ["crawl"] | ["analyse"] | ["crawl", "analyse"]
  created_by:   string    # admin email
  created_at:   timestamp
  last_used_at: timestamp
  is_active:    boolean
  call_count:   number    # 累計呼叫次數
```

---

### 資料關係圖

```
system/config ──────────────── 識別 admin
      │
users/{email} ──────────────── 白名單用戶
      │
      └── usage_log/{id}  ──── 使用量（admin 可查）

projects/{project_id} ──────── 頂層 Project
      │
      ├── owner (→ users/)
      ├── members (→ users/)
      ├── llm_config (Owner 獨有)
      └── analyses/{id}
            └── inputs/{id}

api_keys/{key_id} ──────────── 外部工具金鑰（admin 管理）
```

---

## 10. 參考實作

### 9.1 爬蟲核心參考：Colab v3.8

**檔案**：`~/Desktop/seo_新開發_帶ui介面爬蟲_可輸入多網址.py`  
**版本**：v3.8（2026-06-10）  
**狀態**：已在 Colab 環境驗證可正確突破遮罩的權威實作

此 Colab 腳本是 `content-crawler` 爬蟲核心邏輯的**基準真相（ground truth）**。開發或修改爬蟲時，以此版本為優先參考。

#### 現有 `crawler-service` 已對齊的部分
- OneTrust：`OneTrust.AllowAll()` JS API → 按鈕點擊 fallback
- Fides：`window.Fides.updateConsent()` JS API
- 抽取前移除全部 CMP 容器（OneTrust / Fides）
- `options.page_load_strategy = "eager"`（移除已廢棄的 `desired_capabilities`）
- LLM selector 輔助改用 `google-genai`（`genai.Client`）

#### Colab v3.8 有、現有服務待對齊或評估的部分

| 項目 | Colab v3.8 | 現有服務 | 建議 |
|------|-----------|---------|------|
| `_open()` 重試機制 | 最多 2 次，含逾時偵測 | 無 | 應加入 |
| 每頁硬性時限 | 60 秒 | 無 | 應加入 |
| 頁面載入逾時 | 25 秒 | 15 秒 | 應調整 |
| Chrome binary 尋找 | 多路徑 fallback | 較簡單 | 評估是否需要 |
| 內容過短 fallback | 補入 `og:description` | 無 | 應加入 |
| Dcard 直接跳過 | `UnsupportedSiteError` | 無此判斷 | 應加入 |

#### 現有服務獨有的 Cloud Run 加值（不在 Colab 版）
- `_is_listing_page()`：偵測列表頁並跳過，避免誤抓
- `/api/scrape/batch`：批次端點
- Firestore log callback：即時回報爬取進度

---

## 10. 開發優先順序（草案，未核准）

> ⚠️ 本節為草案，所有設計決策已確認，但尚未進入開發。開發前需取得正式核准。

**第一期：核心分析能力（全新建立）**
1. 建立 `analysis-pipeline` 服務
   - 非同步任務模型（job_id 輪詢）
   - TF-IDF + 語意分群（jieba + scikit-learn）
   - LLM 質性分析（預設 Gemini，支援 Claude）
   - 標準報告生成（Markdown）
   - 用戶 API Key 從 Firestore 讀取並轉傳

**第二期：控制平面重構**
2. 重構 `content-analyser`
   - 移除全域 CRAWLER_LOCK
   - 移除爬蟲協調邏輯（Worker 不再直接呼叫 Crawler）
   - 新增 API 金鑰管理介面（核發 / 撤銷 / 查看使用紀錄）
   - 新增白名單使用者管理
   - 新增服務健康監控（Crawler + Pipeline）
   - 個人設定：LLM 提供商 / 模型 / API Key

**第三期：體驗補全**
3. 歷史專案列表頁（查看過去所有分析）
4. 使用量監控（管理員按用戶查看）
5. 修正現有 Bug（export_utils timestamp、pages 排序）

**第四期：擴展**
6. YouTube 分析（Gemini API 直接分析影片）
7. 其他資料來源整合（視需求）
