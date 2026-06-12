# 待最佳化項目 / Optimization Backlog

記錄已知、但尚未處理的效能與技術債項目。每項註明：問題、影響、建議方向、優先級。
處理完成後移到 changelog 並從本檔移除。

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

*建立於 2026-06-13。新項目請附問題／影響／建議方向／優先級。*
