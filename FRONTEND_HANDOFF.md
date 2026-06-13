# 前端交接文件（FRONTEND_HANDOFF.md）

> 日期：2026-06-13　|　用途：現有前端版面待重新設計，交付前端工程師
> 搭配閱讀：`CODE_REVIEW.md`（完整問題清單）、`CLAUDE.md`（架構與口令制）

## 結論
現有前端（Bootstrap + Jinja2 模板）**版面不符需求，將整體重新設計**。本文件記錄現況、已知 bug、UIUX 待改清單、後端對接點，供前端工程師接手。

---

## 當前技術棧
- **後端框架**：Flask + Jinja2 server-side 模板（`app/templates/`）
- **CSS**：Bootstrap 5.3.3（CDN）+ `app/static/css/style.css`
- **JS**：`app/static/js/app.js`、報告渲染用 **marked.js**（CDN）+ **DOMPurify 3.1.6**（CDN）
- **非同步**：前端 `setTimeout` 輪詢 `dataset_status` / `analysis_status`（GET → JSON）
- **母模板**：`layout.html`（navbar + flash messages + block content）

---

## 🔴 已知緊急 BUG（重做時務必處理）

### BUG-1：分析報告 Markdown 沒渲染（顯示原始符號）
- **現象**：completed 分析報告頁顯示原始 Markdown 文字（`#`、`|`、`-` 等符號），沒轉成 HTML。
- **位置**：`app/templates/analysis_detail.html`（report-content 渲染段）。
- **根因**：用無版本 CDN `https://cdn.jsdelivr.net/npm/marked/marked.min.js`，實際載到 **marked v16.3.0**，與舊用法 `marked.parse()` 不相容（v16 API 變動／可能需不同呼叫方式）。
- **修法建議**：
  1. **鎖版本**：改 `marked@12.0.2`（已確認 CDN 可達、`marked.parse()` 同步回 string 穩定）；或
  2. 用 marked v16 正確 API 並確認非 async 回 Promise。
- **注意**：目前渲染串接是 `DOMPurify.sanitize(marked.parse(...))`（見 BUG 無關的 C3）。**DOMPurify 清毒本身正常**（已驗證：正常內容保留、惡意 `<script>`/`<img onerror>` 被清除、無 alert）。問題純粹在 marked 沒輸出 HTML。

### BUG-2：報告表格無樣式（CODE_REVIEW C4）
- 報告含 Markdown 表格（TF-IDF 關鍵字等），marked 渲染後**無 Bootstrap 表格樣式、無框線**。
- 修法：渲染後對 `#report-content table` 加 `class="table table-striped"`，或注入對應 CSS。

---

## UIUX 待改清單（from CODE_REVIEW.md，重新設計時納入）
| 項目 | 位置 | 問題 |
|------|------|------|
| 分析失敗無重試入口 | analysis_detail.html | failed 只顯示錯誤，須手動回專案頁重做 |
| completed 整頁 reload 閃爍 | analysis_detail.html:40-43 | 輪詢到完成用 `location.reload()`，建議局部更新 |
| 輪詢無逾時提示 | analysis_detail / dataset_detail | job 卡 running 時前端無限輪詢、無回饋 |
| 提交無 loading 狀態 | project_detail.html | 爬取/分析提交按鈕無 disabled+spinner |
| 資料集列窄螢幕擠壓 | project_detail.html:60-82 | 名稱+badge+下載鈕同列，行動裝置擠 |
| 無障礙 | 多處 | 進度條缺 ARIA、emoji 圖示（🔍📄🔧🚀）缺 aria-label |

正面（可保留的模式）：空狀態設計、navbar 響應式 collapse、麵包屑導航。

---

## 頁面清單（app/templates/）
| 模板 | 用途 | 關鍵互動 |
|------|------|---------|
| `layout.html` | 母模板：navbar、user dropdown、flash、admin badge | navbar collapse |
| `login.html`（經 /auth）| 登入頁 | Google OAuth（`<a>` 到 /login）|
| `pending.html` | 等待白名單授權 | — |
| `projects.html` | 我的專案列表（卡片）| 連結進專案 |
| `project_detail.html` | 專案詳情：①爬取表單 ②資料集列表(下載鈕) ③進階JSON分析 ④歷史分析 + 右欄LLM設定/成員 | 多表單、資料集下載鈕、輪詢同步 |
| `dataset_detail.html` | 資料集詳情：爬取進度輪詢、送分析、結果列表、下載MD/JSON | 輪詢、提交分析 |
| `analysis_detail.html` | 分析報告：進度輪詢 / marked+DOMPurify 渲染報告 / 失敗顯示 | **BUG-1/2 在此** |
| `project_new.html` | 建立專案表單 | — |
| `profile.html` | 個人 Gemini Key 設定 | — |
| `admin_dashboard.html` | 管理控制台：服務健康、Secret 更新 | — |
| `admin_users.html` | 白名單管理（批准/拒絕）| — |
| `admin_api_keys.html` | API 金鑰核發/撤銷（供 Colab）| — |

---

## 後端對接點（前端必知）
- **CSRF**：所有 POST `<form>` 必須含 `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`（Flask-WTF CSRFProtect 全域啟用；漏掉會 400）。
- **輪詢端點**（GET → JSON `{status, progress, log}`）：
  - `GET /projects/<pid>/datasets/<did>/status`
  - `GET /projects/<pid>/analyses/<aid>/status`
- **下載端點**（直接觸發瀏覽器下載）：
  - 報告：`/projects/<pid>/analyses/<aid>/download`（.md）
  - 資料集：`/projects/<pid>/datasets/<did>/download.md` 與 `/download.json`
- **權限**：守衛在 `app/auth_guards.py`（login_required，需 approved）+ `project_access_required`（viewer/editor/owner）。
- **報告資料**：`analysis.result_markdown`（Markdown 字串）放在 `<textarea id="md-source" hidden>`，前端 JS 取 `.value` 渲染。

---

## 當前部署 / Git 狀態（交接時）
- **正式 revision**：`content-analyser-00009`（已含 C3 的 DOMPurify，但 BUG-1 marked 未解）。
- **未合併分支**：`feat/fix-c3-xss`（C3 DOMPurify commit，尚未 merge main）。前端重做 `analysis_detail.html` 時可能覆蓋此檔，請與後端確認是否保留 DOMPurify 清毒邏輯（**建議保留**，安全必要）。
- 測試報告 doc `analyses/test_c3_render` 已刪除。

## 後端未完成項（非前端，見 CODE_REVIEW.md）
- C1 crawler SSRF、C2 analysis LLM JSON 解析穩健性、C5 crawler scroll 逾時保留部分結果。
