# DEVELOPMENT.md — 開發總文件（開發中樞）

> **支柱 2／3：開發。** 本檔是開發相關文件的索引中樞：記錄每份開發文件的用途與
> 「何時該更新它」。實際內容分散在各被連結的文件中，本檔不重複內容、只維護索引與現況。
>
> 三支柱：[產品規格](product_guideline.md)｜**開發總文件（本檔）**｜[維護總文件](MAINTENANCE.md)
> 規範：[CLAUDE.md](CLAUDE.md)（開發治理／口令制）｜[deploy.md](deploy.md)（⛔ 部署鐵則）

---

## 1. 開發治理（摘要）

完整規範見 [CLAUDE.md](CLAUDE.md)。核心：

- **先計畫後執行、最小變更、可回溯。** 任何檔案/部署/Git 變更前先提案、等口令。
- **批准口令制**：`核准開發`（新功能）/ `核准修正`（bug）/ `核准改善`（重構）/ `核准執行`（一次性）/
  `核准回復`（回退）/ `核准推送`（push）/ `核准部署*`（部署，見 [deploy.md](deploy.md)）。
  「可以/好/OK」不構成授權。
- **Git 流程**：功能走 feature branch → 本地驗證 → 部署測試 → merge `main` → 推送。`main` 永遠 = 已部署且驗證的穩定版。
- **四服務邊界**：只透過 HTTP API 溝通，不跨服務 import；主程式不裝 Chrome、不直接協調爬蟲。

---

## 2. 現況里程碑

- **Phase 0–4 已完成**（清理地基 → 爬蟲補強 → 分析引擎 → 控制平面 → 整合）。原始藍圖見 [development_plan.md](development_plan.md)。
- **四服務上線**：content-analyser / content-crawler / analysis-pipeline / search-extent（§7 真實接地，阻塞於 Google Ads dev token）。
- 近期重點（詳見 [changelog.md](changelog.md)）：白名單/OAuth、CSRF、資料治理（刪除/更名/取消/usage）、
  爬蟲穩健性大改（看門狗/自動續批/子集合/廣告封鎖）、分析並行化加速、proxy 憑證遷 Secret Manager、後台金鑰治理。

---

## 3. 文件索引（開發類）

| 文件 | 用途 | 何時更新 |
|------|------|---------|
| [development_plan.md](development_plan.md) | 分期開發藍圖（Phase 0–4，已完成）+ 研究/待開發項目（YouTube 影片資料化、Cowork 整合等） | 規劃新功能、新增研究項目、調整里程碑時 |
| [CODE_REVIEW.md](CODE_REVIEW.md) | 全專案程式碼審查記錄（2026-06-13）：Security/Correctness/Performance/Maintainability 各維度問題清單 | 做程式碼審查、發現缺陷、或解決清單項目時（標註狀態）|
| [OPTIMIZATION.md](OPTIMIZATION.md) | 效能與技術債 backlog（每項：問題/影響/建議/優先級）。處理完移到 changelog 並移除 | 發現技術債、完成最佳化時 |
| [SECURITY_INCIDENTS.md](SECURITY_INCIDENTS.md) | 安全事件記錄（prompt injection 等）+ 本平台高注入風險面與防範 | 遇到安全事件、或調整安全防範策略時 |
| [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md) | 前端現況、已知 bug、UIUX 待改、後端對接點（前端重設計交接用） | 前端交付/改版、頁面或對接點變動時 |
| [changelog.md](changelog.md) | 變更/部署歷史（每次修改與部署的完整記錄） | **每次修改/部署後**（強制）|

> 安全事件與審查的修正彼此呼應（如 SSRF / LLM JSON 解析 / XSS）；做安全相關開發時兩份對照看。

---

## 4. 開發時怎麼用這些文件

依「你要做什麼 → 更新哪份」對應，職責單一、不交叉重複：

- 規劃新功能／研究 → [development_plan.md](development_plan.md)
- 做審查或記錄缺陷 → [CODE_REVIEW.md](CODE_REVIEW.md)
- 發現/清掉技術債 → [OPTIMIZATION.md](OPTIMIZATION.md)
- 遇到安全事件 → [SECURITY_INCIDENTS.md](SECURITY_INCIDENTS.md)
- 前端改動 → [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md)
- 改了任何東西（尤其部署後）→ [changelog.md](changelog.md)
- 改到產品行為/規格 → [product_guideline.md](product_guideline.md)（產品支柱）
- 改到部署/腳本/金鑰/技術棧 → [MAINTENANCE.md](MAINTENANCE.md)（維護支柱）
