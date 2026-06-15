# 待最佳化項目 / Optimization Backlog

> 📍 屬【開發支柱】，由 [DEVELOPMENT.md](DEVELOPMENT.md) 索引。
> 記錄已知、但尚未處理的效能與技術債項目。每項註明：問題、影響、建議方向、優先級。
> 處理完成後移到 [changelog.md](changelog.md) 並從本檔移除。
> 已處理：O-1（批次重用 driver）、O-4/O-5（已加重爬未完成/全部的 recrawl 功能）；分析端已並行化加速。

---

## 🔴 高優先

### O-1. undetected-chromedriver 冷啟動 48 秒，且批次每篇重複初始化　（部分完成 2026-06-13：批次已重用 driver）

> ✅ 已實作「批次內重用 driver」（crawl_job 單一 crawler + scrape keep_driver=True），
> 省去每篇 48s 冷啟動。剩餘可選優化：指定 UC version_main 避免每次 patch、
> 或評估 selenium-stealth 加速首次啟動。以下為原始記錄。


- **問題**：`content-crawler` 每次 `scrape()` 都 `new HeadlessCrawler()` → `_init_driver()`，
  undetected-chromedriver 在 Cloud Run 初始化約 **40–50 秒**（patch chromedriver、版本檢查）。
  批次爬取時，每個 URL 各初始化一次（≈48s × N），導致爬 20 篇可能多花 15+ 分鐘純在啟動 driver。
- **影響**：爬取整體很慢、Cloud Run CPU 時間與費用偏高、使用者等待久。
- **發現於**：2026-06-13 端到端測試，logs 顯示 `[INIT]` 階段耗 48 秒。
- **建議方向**（擇一或併用）：
  1. **批次內重用 driver**：`crawl_job.run_crawl_batch` 建立一個 crawler 實例，逐一爬完才 close
     （需評估 Colab 版「每篇重置 driver 防崩潰」的穩定性取捨，可加「每 N 篇或遇崩潰才重啟」）。
  2. **指定 UC `version_main`**，避免每次啟動都做版本偵測/patch。
  3. 評估改用標準 selenium + selenium-stealth（啟動快），UC 僅在被反爬擋下時才用。
- **注意**：批次重用 driver 時，硬性時限 deadline 已改為「driver 初始化後」才計時（commit e601adc），
  重用情境下單篇 deadline 仍正確（每篇重設 deadline）。

---

## 🟡 中優先

### O-2. 分析任務 / 爬取任務的 Firestore 輪詢成本

- **問題**：content-analyser 前端每 3 秒輪詢一次 status，後端再去 crawler/analysis 查 job，
  每次都讀寫 Firestore。長任務（數分鐘）會累積不少讀寫。
- **影響**：Firestore 讀寫次數隨任務時長線性增加；規模大時成本上升。
- **建議方向**：輪詢間隔動態調整（剛開始密、後期疏）；或評估 SSE 推送取代輪詢。

### O-3. analysis 結果 `result_markdown` 直接存 Firestore 文件

- **問題**：完整 Markdown 報告存在 `analyses/{id}.result_markdown`，大報告可能接近 Firestore
  單文件 1 MB 上限。
- **影響**：超大報告（上百篇素材）可能寫入失敗。
- **建議方向**：超過閾值時改存 Cloud Storage，Firestore 只存連結。

---

## 🟢 低優先 / 體驗

### O-4. 批次爬取單篇失敗無重試
- 目前單篇 timeout / 失敗即記為 failed，不重試。可加「失敗篇可單獨重爬」的 UI。

### O-5. 資料集無法增量補爬
- 資料集爬完後，若想補幾個網址，目前需新建資料集。可加「補爬」功能。

---

---

## 2026-06-15 技術債盤點（已拆為背景任務 chip 追蹤）

一次完整唯讀盤點的結論（已修正盤點 agent 的高估後）。多數已開背景任務處理：

| 項目 | 嚴重度 | 說明 |
|------|--------|------|
| 自動化測試缺失 | High | 四服務僅 `py_compile` 把關、無 pytest；應補關鍵單元測試（白名單、LLM JSON 解析、爬蟲 client）|
| crawler.py 巨型函式 + 硬編站台設定 | Medium | `_extract_main_text`(~309行)/`scrape()`(~235行) 過長；SITE_TEMPLATES(34站)/AD_BLOCKLIST 硬編，宜拆函式 + 移 Firestore |
| 重複邏輯 + 散落常數 | Medium | submit-analysis ×3、LLM JSON 清理 ×2、collection 路徑/預設模型/`MAX_*` 散落 → 抽 helper + 集中常數 |
| `except Exception: pass` 靜默吞錯 | Medium | 三服務多處吞掉根因 → 至少改為記 log |
| 未釘版依賴 | Medium | analysis-service 的 google-genai/anthropic/openai/requests 未 pin |
| LLM 韌性 | Low | llm_client 無 rate-limit 重試/退避；取消檢查點缺口；600s thread 逾時後仍續跑 |

> 已完成（不在此清單）：H1 deploy.sh 防清 env + proxy→Secret Manager 標準化、H3 文件四服務同步、
> 服務驗證金鑰後台鎖定 + rotate-key.sh。詳見 [changelog.md](changelog.md)。

---

## 2026-06-15 全面 code review / 安全審查 — 剩餘追蹤項

完整審查後，**多數 Critical/High/Medium 已修正並提交**（SSRF DNS 解析、prompt injection 防護、
SECRET_KEY fail-fast、安全標頭、前端 SRI、LLM 退避重試、Path 逾時 race、續批副作用 gate/防重複、
分析上限、_seq 原子化、金鑰防洩、cleanup 收斂、選擇器驗證…見 changelog）。以下**較大、需獨立處理**
的項目納入開發程序追蹤：

| 項目 | 嚴重度 | 說明 / 建議 | 狀態 |
|------|--------|------------|------|
| crawler SSRF redirect 殘留 | High | 入口 `_is_safe_url` 已解析 DNS 收口；但 og/oEmbed/YouTube 的 urllib 抓取與 Chrome 導航**跟隨 redirect 時未重驗** → 公開 URL 302 到內網仍可能。需 redirect handler 逐跳重驗 / egress proxy | 背景 chip |
| crawler 同步 /api/scrape 無看門狗 | High | async 批次（crawl_job）有看門狗；同步單篇路徑步驟內 hang 無上限。應比照包 `ThreadPoolExecutor.result(timeout)` | 背景 chip |
| crawler 回收/recycle close() 可能卡住 | High | 看門狗砍掉 hung thread 靠 `old.close()`；若 close 本身 hang 會卡整批。應對 close 加超時 | 背景 chip |
| content-analyser list_projects/list_all_users 全集合掃描 | Medium | 每次列表掃整個 collection 查 member；應加 `member_emails` array 欄位 + `array_contains` 查詢 + 既有專案 backfill | 背景 chip |
| 分析平行階段缺取消檢查點 | Low | 兩路平行（最貴 LLM 段）期間無 `cancel_requested` 檢查；取消後仍跑完才停 | 背景 chip |
| C5 爬蟲 scroll 逾時保留部分結果 | Medium | 清單頁滾動逾時整篇 fail，應保留已抓內容（CODE_REVIEW C5，待確認現況） | 追蹤 |
| search-extent §7 真實接地 e2e | — | 阻塞於 `ADS_DEVELOPER_TOKEN`（待 Google Ads Basic 核准） | 阻塞 |
| 研究項目（未核准）| — | YouTube 影片資料化、Cowork 蒐集整合（見 development_plan.md） | 待核准 |

---

*建立於 2026-06-13。新項目請附問題／影響／建議方向／優先級。*
