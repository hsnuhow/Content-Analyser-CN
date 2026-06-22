# Changelog

## 2026-06-22 新增：資料集逐項管理（看單條 / 編輯內容 / 勾選送分析）——僅系統爬取資料集
讓使用者在送分析前整理爬取結果（接續付費牆「標示不完整」）。**手動匯入資料集不開放**（已自行篩過）。
- **勾選送分析**：爬取資料集每個成功項加勾選框（全選鈕），「送去分析」只送勾選項；**不完整(付費牆)項預設不勾**。analyse_dataset 收 selected_ids、與成功項取交集；未送則維持全部（向後相容）。
- **看單條 / 編輯**：每個成功項「🔍 檢視」彈窗，按需向新 `GET items/<id>` 取全文（列表頁不內嵌全部內文，大資料集不卡）；can_curate 時可編輯內文/標題 → `POST items/<id>/edit` 存回、重算字數、清除不完整標記、標 edited（重爬會覆蓋）。viewer/手動匯入為唯讀檢視。
- 純 content-analyser：datasets.py +2 路由 + analyse 收選取；dataset_detail.html 勾選欄/檢視鈕/彈窗；dataset_detail.js 全選/注入選取/彈窗載入+存檔。

## 2026-06-22 新增：付費牆截斷內容「標示不完整」
天下/商周/端傳媒/工商/UDN/鏡週刊 等站常在付費牆前只給少量預覽——這對分析是不完整的，需標示。
實爬 6 站歸納兩型，雙路偵測（皆後台可編 floor+Firestore）：
- **A 明確 CTA**（天下型）：內容含「訂戶限定/解鎖/查看訂閱方案/不限篇數暢讀/立即購買」等標記 → incomplete=paywall。
- **B 靜默截斷**（商周/端傳媒型，付費牆是 JS 遮罩、抓不到 CTA）：已知付費網域 + 內容短於門檻 → incomplete=paywall_short。
實作：page_classify.detect_paywall_incomplete（純函式、設定注入）+ crawler_config.get_paywall_config
（floor: 17 標記 + 3 網域門檻；Firestore crawler_config/paywall admin 可加）+ crawler._success
（各抽取路徑集中標記，內容保留不丟棄）。result/item 加 incomplete + incomplete_reason。
標示：資料集頁「⚠️ 不完整」標籤 + 說明；Markdown/JSON 匯出也標註。
驗證：page_classify 4 新測試（CTA命中/短截斷/完整不標/付費網域長免費文不誤標）+ 全測試通過。

## 2026-06-22 廢除 Tier 2（Gemini URL 直讀）
實測證明 Tier 2 無效後直接移除（見研究：Gemini url_context 透過 API 連維基百科都讀不到、是該工具本身不穩，對 Cloudflare 難站如 Dcard 亦無解；且官方文件還推薦維基當測試）。
- `tiered_fallback.py`：移除 `gemini_url_read` + `is_gemini_url_fallback_enabled`；`run_tier23` → `run_tier3`（移除 Tier2 區塊 + 不再需要的 gemini_api_key 參數）；模組 docstring 記錄廢除原因。
- 呼叫端 `app.py` / `crawl_job.py` 改呼叫 `run_tier3`、移除 Tier2 相關。
- 行為：分層降為 Tier 1 → Tier 3（住宅代理，仍預設關閉）。難站改走 Tier 3 或 Cowork（真實瀏覽器+住宅 IP）。
- 驗證：無任何 Tier2 殘留引用、pyflakes 乾淨、import 解析回歸測試 + 全測試通過。

## 2026-06-22 程式碼審查模組 4–6（三後端服務）+ 未用 import 檢驗清理（三並行代理審 + 親驗 + 逐服務部署）
三個後端服務（crawler 5754 / analysis 4702 / search-extent 812 行）以三個並行審查代理掃，發現親自驗證後逐服務部署驗證。
- **🔴 修正(高，回歸)**：`crawler-service/image_extract.py` 從 `crawler` import `SITE_TEMPLATES`（站台模板外部化時改成 get_site_templates）+ `MAIN_CONTENT_SELECTORS`（抽取層三層化時搬到 dom_extract）→ `/api/extract-images` 一觸發就 ImportError 500（延遲 import，py_compile 掃不到）。改從 `site_templates.get_site_templates()` + `dom_extract.MAIN_CONTENT_SELECTORS` 取，順帶讓影像擷取也吃後台可編模板。**實測：25 URL 共 116 張大圖，功能恢復。**
- **修正**：`search-extent/brand_presence.py` 內層 ThreadPoolExecutor 綁 `min(8, len(chunks))`，避免與外層每品牌池巢狀執行緒爆量。
- **改善**：`analysis-service` cluster_id 直接索引統一改 `.get`（pipeline/synthesis/report，防 KeyError 靜默吞 search-extent）+ nlp_path 關聯規則 lift 除零 guard；`search-extent` `_ground/_ground_brand` 移除 tries=1 死碼。**實測：discover 42 候選、grounding 正常。**
- **新增回歸守衛**：`tests/test_imports_resolve.py`——AST 跨模組 import 解析檢查，一次抓出「重構搬走名稱後忘改 import」的延遲 import 回歸（py_compile 抓不到，正是本次 image_extract 那類）。
- **未用 import 檢驗清理**：content-analyser pyflakes 58 → 6（移除 52 個死 import）。**刻意保留 6 個並加 noqa+註解**：firebase 初始化 side-effect（app/__init__ services）、route 註冊 side-effect（project_routes/__init__ 四子模組）、admin_routes re-export（_load_dataset_items）。驗證無 undefined name、四子模組路由實測全 200（route 註冊未破壞）。
- 部署：crawler 00092 / analysis 00054→新 / search-extent 00008→新 / content-analyser 00100。

## 2026-06-21 程式碼審查與最佳化（模組 1–3：前端 JS / 業務模組 / 路由層；逐模組部署驗證）
系統性逐模組審查（找 bug + 邊界 + 效率不佳寫法 + 補註解），每模組部署後實測。
- **模組 1 前端 JS**：① 抽 `app.js` 共用 `poll(url,opts)`（統一嘗試上限/終態/退避，根治「失控輪詢」類 bug）+ `renderMarkdown`（marked/DOMPurify 載入守衛）；6 處手刻輪詢收斂成 1 份。② dataset_detail.js 3 個 poll 補上嘗試上限（原本無上限，卡非終態會永遠打伺服器）。③ 複製成敗才回饋。④ admin_terms esc 補單引號。實測：analysis 頁 renderMarkdown 端對端、選擇器候選頁無重整、各頁無 console 錯誤。
- **模組 2 業務模組**：① 修正 doc_extract 上傳純文字加編碼回退（UTF-8→cp950/Big5），台灣 .txt 不再亂碼（與 admin _extract_text 既有多編碼一致）。② crawler_client(5)+analysis_client(8) 共 13 個 HTTP 包裝抽 `_request_json`（mock 行為對照一致）。③ datasets_store 三處逐筆 delete 改 batch。實測：gq 爬取 2117 字零回歸。
- **模組 3 路由層**：審查 auth_guards/routes/project_routes(package)/admin_routes——品質高、無 bug、無安全/效率問題（三級存取控制 + 白名單 gate + N+1 索引查 + Firestore dot-bug 防護 + 跨提供商金鑰外洩防護皆到位）。僅移除 modularization 殘留的 6 處重複 inline import（含一段 dataset_export 死碼）。實測路由正常。
- 全程純 content-analyser，API 介面不變；部署 00097→00099。

## 2026-06-21 重構：資料/邏輯分離——站台模板 + 爬蟲垃圾詞外部化到後台（Phase A/B，已部署 crawler 00091 / analyser 00096）
依用戶原則「**單一網站專屬的資料 → 後台可編；通用基礎 → 程式內 floor**」，把爬蟲裡寫死的站台資料抽到 Firestore，admin 可增/改/刪、**不必改碼部署**。沿用 `get_ad_blocklist`/`get_term_filters` 範式（floor + Firestore + 60s 快取 + 讀失敗回退）。
- **Phase A 站台抽取模板**：`site_templates.get_site_templates()` = 內建 37 模板（floor 安全基線）+ Firestore `crawler_config/site_templates.templates`（admin 以模板名為 key 覆寫/新增）。`crawler._content_container_known` / `dom_extract` Phase 2.0+2.1（3 處）改用之。已 seed 37 模板入 Firestore。admin 頁 `/admin/site-templates`（JSON 編輯 + 結構驗證）。驗證：gq 爬取 logs `Matched template: 'gq_tw'`（讀 floor+Firestore）、內容 2117=基準零回歸；admin 頁顯示 37。
- **Phase B 爬蟲垃圾詞（尾部樣板）**：`text_clean.TRAILING_BOILERPLATE` 只留通用詞（版權/訂閱/下載 CTA）作 floor；單一媒體專屬詞（中央社/自由/鏡週刊/TechNews 的贊助·訂閱 CTA，25 個）externalize 到 Firestore `crawler_config/junk_keywords.boilerplate`。`trim_trailing_boilerplate` 加 `extra_terms` 參數（**呼叫端注入 → text_clean 維持純函式**）；新 `crawler_config.get_extra_boilerplate()` 只做 Firestore I/O（floor 不在此），`crawler._trim_trailing_boilerplate` wrapper 注入。admin 頁 `/admin/junk-keywords`（textarea）。自測：floor 截斷、extra 注入截斷、未注入不截斷（證明已移出 floor）。
- 設計：純函式模組（text_clean/dom_score/site_templates）維持純粹，Firestore I/O 集中在 crawler_config / get_site_templates；floor 永遠是安全網（Firestore 空/掛仍可運作）。

## 2026-06-21 修正（UI/JS）：選擇器候選頁無限重整、按鈕點不到
- **根因**：`research_url_status` 用 `session['_research_job']` 記住研究 job，但完成後沒清 → 每次載入都輪詢到那個 completed job → JS 在 completed 分支 `location.reload()` → 又 completed → 無限重整（P1 自動研究產生大量 completed job 後變常態）。
- **修**：① JS `admin_selector_candidates.js` 加 `sawInProgress`——只有真的觀察到「進行中→完成」轉換才 reload；一進頁面就是 completed（session 殘留）不 reload，迴圈當場斷。② server `research_url_status` 終態（completed/failed）後 `session.pop('_research_job')`，下次載入回 none。驗證：部署的 JS 含修正、頁面 200、status 端點回 none。

## 2026-06-21 智慧化：爬蟲選擇器「自動學習迴圈」補完（P1/P2/P3/Q1/P4，已部署 00086~00089，每刀實測）
研究現有智慧子系統（research.py 閉環 agent / site_learning / 轉移偵測都已存在）後，資料驅動（crawl_telemetry：body_fallback 23+failed 13 為最大品質破口）把片段自動化 + 補強可靠性，串成完整迴圈：**爬→失敗→自動研究→高信心自動升級/弱候選→admin/被擋→診斷拋用戶→learned 失效→自癒重學**。
- **P1 爬完自動觸發研究**（content-analyser `dataset_sync`）：完成時收集 failed（items.status）+ body_fallback（job results.resolved_by）→ 經 `crawler_client.submit_research` 自動跑研究 agent（原本要用戶手動點）。editor+ 觸發、一次性 flag、上限 50。實測：PDF 失敗 → 自動建研究 job。
- **P3 研究候選品質收緊**（`research.py`）：候選階段就擋過寬選擇器（main/body/article，`normalize_selector`+`_is_valid_selector`）+ 用 `page_classify` 偵測挑戰/錯誤頁殘片（`is_error`）+ 被擋站正確診斷。實測 dcard：弱候選 `main`/302 → 改吐清楚 `blocked_or_error` 診斷。
- **Q1 強化自動轉移偵測**（新 `url_drift.py` 純函式）：舊版只比可註冊網域 → 補同域軟轉址（登入/同意子網域、文章→首頁、login/error path），**保守避開合法 redirect**（http→https/尾斜線/m.行動版/locale）。14 單元測試 + udn 無誤判實測。result 回 final_url。
- **P2 高信心候選自動升級**（`run_research`）：2+ 樣本跨驗證 + 無診斷 + ≥800 字 → 跳過 admin 直接進 learned（少介入）。三重安全網（P3 正規化 + save_learned 再驗 + 讀取端驗證）。
- **P4 已學選擇器失效自癒**（`site_learning.note_learned_outcome` + `dom_extract` Phase 2）：learned 命中有效→fail_count 歸零；連續失效 3 次→自動降級刪除→走模板/啟發式→再失敗 P1 重學。實測 autos.udn.com 命中 learned → fail_count:0。
- 新增測試：url_drift 14 例。先前遙測經 Firestore REST 唯讀取得（resolved_by 分布、21 learned 網域、11 候選全 approved）。

OPTIMIZATION 標 High 兩項，非同步 crawl_job 早有、同步 `/api/scrape` 路徑原本缺：
- **看門狗**：`app.py:_tier1_scrape` 外包 `ThreadPoolExecutor + result(timeout=min(hard_timeout+30, 290))` → scrape 步驟內 Selenium 指令 hang（內部 hard_timeout 擋不住阻塞呼叫）強制上限，留在 Cloud Run 300s 請求上限內。
- **close() 超時**：新增 `_force_close()`，close() 包 15s 上限、逾時直接 kill Chrome 進程（對齊 `crawl_job._force_close`）。
- 自包含於 app.py 同步路徑，**不動已驗證的 async 批次（crawl_job）**；行為保留（看門狗只在真 hang 時觸發）。async 路徑實測無回歸（gq 2117/tvbs 2259/example 145 與基準一致）。

## 2026-06-21 重構：crawler.py 抽取層三層化完成（2569→1423 行，-45%，每刀 Porsche before/after 零回歸）
延續 dom_score（評分）後，完成 God Object 分解。**crawler.py 由 2569 → 1423 行（-45%）**，抽取層三層分離、依賴單向無循環：
- **Layer 1 純函式（leaf，可單元測試）**：`page_classify`(錯誤/封鎖頁,73) / `text_clean`(清文字,60) / `dom_score`(DOM評分+dom_summary,229) / `dom_parse`(JSON-LD/RSC/meta/列表頁/CMP,191) / `site_templates`(362行站台模板資料,364)。
- **Layer 2 協調**：`dom_extract`(393)——`_extract_main_text` 348 行五階協調（已學/快取→模板→結構化→啟發式評分→LLM）抽出；driver/LLM 經 callback 注入（ask_gemini_fn/cross_domain_drift_fn）、domain_cache 傳參、resolved_by 回傳。
- **Layer 3 driver**：`crawler.py`(1423)——只剩 Chrome 生命週期/導航/滾動/scrape 主流程/特殊來源。
- **依賴方向**：crawler → dom_extract → {dom_score,dom_parse,text_clean,site_templates,site_learning}（皆 leaf）。AST 證實全程 DAG、無循環依賴（原問題是 God Object 高耦合，非循環）。
- **驗證方法論**：rollback 先建 + Porsche 11 模板站 before/after 實測（盯 resolved_by 模板選對 + 內容長度）。各刀：dom_score 11/11 長度同+7/7 模板；dom_parse 10/11+JSON-LD 解析器運作；site_templates git diff 證逐字相同；dom_extract 9/11（2 為動態頁）+8 模板+structured 2=2+heuristic byte 同。純資料/邏輯搬移用 git diff 證逐字（比爬取強）；協調移走用 before/after + pyflakes 無 undefined（替換完整）+ 8 解出點 resolved 手動複查。新增測試：dom_score 9 + dom_parse 9 例。

## 2026-06-21 重構：crawler DOM 節點評分抽出 dom_score.py（真實 Porsche before/after 實測零回歸）
先前評估 DOM 評分群「簡單頁無法驗抽取品質」而暫緩；本次用**真實文章 before/after 對照**解掉驗證難題後執行。
- **抽出**：`_calculate_node_score/_visual_weight/_dom_depth/_paragraph_quality/_chinese_ratio/_confidence/_looks_like_listing_block/_looks_like_cookie_banner/_css_path/_get_element_depth` → `dom_score.py` 純函式（無 driver/state，self._log 經 log_fn）。4 個只被 node_score 呼叫的子評分完全移走、6 個外部有呼叫的留薄方法委派 → **`_extract_main_text` 協調（模板選擇/resolved_by/階段順序）零改動**。邏輯逐字複製。
- **AST 研究先行**：證實 crawler-service 模組層 + class 內 48 方法呼叫圖**皆為 DAG、無循環依賴**；問題是 God Object（高耦合），非循環。評分群 10 方法 8 個零 self 依賴 → 最乾淨可抽。
- **驗證（rollback 先建：snapshot-20260621-pre-crawler-dom-refactor / rev 00080）**：Porsche 11 模板站建 scratch dataset，before(00080)→重構→after(00081) 同 URL 對照。**11/11 內容長度逐站完全相同**；**7/7 模板選擇器命中一致**（ettoday/gq_tw/harpersbazaar_tw/ltn/tvbscars/udn/vogue_tw）；structured 2=2；heuristic kingautos 887=887 byte 相同（評分數學保留鐵證；udn 404 頁 0.5 分差為動態頁變異、選同節點）。dom_score 9 測。
- 仍暫緩：`_extract_main_text` 主協調瘦身、`_build_dom_summary`/`_ask_gemini_selector`（選擇器學習）。

## 2026-06-21 重構：crawler.py 部分模組化（page_classify + text_clean，刻意收手）
crawler.py（2569 單一大 class HeadlessCrawler）抽出兩個純函式模組，每刀部署後實跑爬蟲驗證（成功 1/略過 0/失敗 0）、pyflakes 把關、characterization 測試：
- `page_classify.py`：`_looks_like_browser_error/http_error/block_page` + 三組 marker 常數（純 bool 分類、無 driver/_log），6 處呼叫點改用 module 函式，9 測。
- `text_clean.py`：`_clean_text` / `_trim_trailing_boilerplate`（+ _TRAILING_BOILERPLATE）；crawler.py 保留同名薄方法委派（呼叫點全不變、self._log 經 log_fn 傳入），9 測。
- **DOM 內容評分群（_calculate_node_score 等）刻意暫緩**：彼此深度互呼叫、是「從真實文章選正文」的核心抽取品質邏輯，簡單頁爬取無法可靠驗證抽取品質回歸 → 風險/報酬不佳，記為已評估技術債（與 project_routes 獨立 route handler 性質不同）。

## 2026-06-21 安全：crawler-service 漏洞審查 + 三項修補（已部署，每項實跑爬蟲驗證）
多代理安全審查 crawler-service（風險最高：抓任意 URL + 跑 Chrome + 執行 JS + LLM 選擇器）。找到 1 High + 2 Medium + 2 Low，已修可利用的三項：
- **🟠 High SSRF（Chrome 路徑繞過 net_guard）**：`net_guard.safe_urlopen` 只擋 urllib；Chrome（`driver.get`）自行解析 DNS + 自動跟隨 redirect，可被 DNS rebinding（TTL=0 翻 169.254.169.254）或 redirect→內網繞過，讀 GCP metadata（SA token）。修：`net_guard.is_safe_ip(ip)` + `_init_driver` 開 performance log + `_open` 後 `_assert_safe_remote_ip` 取主文件實際 remoteIPAddress 再驗，命中內網拋 UnsupportedSiteError 丟棄；fail-open 不誤殺合法站。涵蓋 scrape/extract-images/research 三端點。實跑：公網 httpbin/example 成功 2/略過 0/失敗 0、IP 檢查未誤殺。
- **🟡 ReDoS**：`_extract_from_json_ld` 的 `(?:[^"\\]|\\.|\n)*` 歧義量詞（`|\n` 在 DOTALL 下與 `[^"\\]` 重疊）→ 移除冗餘 `|\n`，行為等價、消除回溯。
- **🟡 金鑰落地**：`/api/crawl/batch` 把解析後 gemini_api_key（含系統 GENAI fallback）放進 Cloud Tasks body 明文持久化 → 改存 access-controlled 的 `crawl_jobs/{job_id}.gemini_api_key`（僅使用者金鑰），worker 從 job doc 讀回、系統金鑰只在 worker 從 env/Secret 解析、永不進佇列；research/extract-images 本就不帶金鑰。
- 測試：net_guard 測試 +4（is_safe_ip）共 19 例。**🔵 Low 未處理**：error 訊息夾帶 URL/例外、背景 fallback 無並行上限（已知）。**根治建議（你的 infra）**：Cloud Run egress 封 metadata/內網（縱深之外的網路層根治）。
- ✅ **已做對的防護**：urllib 逐跳 SSRF 重驗、API key hmac.compare_digest、LLM 選擇器只進 BeautifulSoup 不進 JS、抓取讀取上限、proxy 帳密 json.dumps、金鑰不進 log。

## 2026-06-21 重構：巨檔模組化（nlp_path + project_routes 徹底拆分，已部署，每刀 Chrome 驗證）
零回歸、每刀部署後 Chrome MCP 實測、rollback.sh + snapshot 隨時可退。新增 tests/（pyflakes 把關每次拆分無 undefined name）。
- **nlp_path.py 883→663**：抽出 `text_processing`（文字/來源層：jieba 斷詞/來源分類/停用詞/過濾清單；可本地測 12 例）。真實分析驗證（關鍵字/分群/情感全產出）。
- **project_routes.py 2366 → package + 8 模組**：
  - **策略 B（抽業務邏輯層）**：`url_utils`(URL 正規化, 12測) / `datasets_store`(items 子集合) / `dataset_export`(MD/JSON, 6測) / `doc_extract`(上傳檔→文字, 7測) / `analysis_store`(分析狀態對帳) / `dataset_sync`(爬取同步) / `llm_models`(供應商模型, 8測) / `project_lifecycle`(刪除/active jobs)。
  - **策略 A（blueprint package 分領域）**：`project_routes/` package = `__init__`(146, 純 core：bp + 共用 helper + decorator + admin re-export + 4 子模組註冊) + `projects.py`(356, ①CRUD+②LLM設定) + `analysis.py`(545, ③分析+⑥整合) + `datasets.py`(731, ④資料集) + `discovery.py`(124, ⑤探勘)。URL/blueprint 名不變、admin re-export 不變。
  - 順帶修：A0 的 `^from .` regex 漏改縮排 lazy import（discovery 的 search_extent，A1 改 module-level 修復）。
- 測試基礎：先前零測試 → tests/（無 pytest 可 `bash tests/run.sh`）+ FakeFirestore 替身 + pyflakes 拆分把關。

## 2026-06-21 修正(N+1) + 補強：list_projects 索引查詢 + 測試環境機制（已部署）
- **N+1**：`list_projects` 非 admin 由全表 `.stream()` 改為 owner 等值查 + `member_emails` array_contains（兩個索引查詢）；admin 全站視角仍全掃（單一管理員）。projects 加 `member_emails` 陣列（= members.keys()），create/add/remove_member 三處同步維護。owner 永遠由 owner== 查、不依賴新欄位 → 零空窗。既有專案以一次性 admin 路由 `/admin/backfill-member-emails`（伺服器端 SA 權限、冪等）補欄位（已跑：5/5）。附錄 C 更新。
- **測試環境機制**（拆巨檔前的安全網）：`requirements-dev.txt`（pytest）+ `pytest.ini`（pythonpath 指向各服務）+ `tests/conftest.py` + `tests/fakes.py`（FakeFirestore 測試替身，讓 db 相依邏輯可在無真 Firestore 下測）。維持無 pytest 也能 `bash tests/run.sh`。全 42 tests 過（含 N+1 查詢邏輯驗證）。
- **驗證**：Chrome 全面驗證——專案列表（5 專案）、project_detail（5 分析/資料集/面板/模型選擇器）、analysis（渲染 45344）、dataset（25 項）、admin 控制台，全部取資料正常、零回歸。

## 2026-06-21 補強：CSP 移除 script-src 'unsafe-inline'（8 頁 inline JS 全外部化，已部署）
技術債暫緩清單最後一項。先建可重用範式、逐頁遷移 + 逐頁 Chrome MCP 實際驗證，最後才移除 unsafe-inline。
- **範式**：inline `<script>` → `static/js/*.js` + addEventListener；Jinja 變數走 `data-*` 屬性；
  Markdown 走 `<script type="application/json">` 資料島；簡單 confirm/submit/copy 走 app.js 全域 helper
  （`data-confirm` / `data-confirm-click` / `data-submit-form` / `data-copy-target`，事件委派、只對帶屬性元素生效）。
- **遷移 8 頁**：login、admin_terms、admin_selector_candidates、admin_api_keys/users/knowledge/dashboard（app.js helper）、
  derived_report、analysis_detail、dataset_detail、project_detail。新增 7 個 per-page js + app.js helper。
- **CSP**：`script-src` 由 `'self' 'unsafe-inline' cdn.jsdelivr.net` → `'self' cdn.jsdelivr.net`（移除 unsafe-inline）。
  `style-src` 仍保留 unsafe-inline（inline 樣式尚多、XSS 風險低，後續處理）。
- **驗證**：每頁部署後 Chrome MCP 實測（互動 + console），最後嚴格 CSP 下確認 project_detail（模型選擇器/slider/按鈕）、
  analysis_detail（markdown 45344 字渲染）全正常 = 無漏網 inline。擴充注入的 inline script 跑在 isolated world、不受頁面 CSP 管。
- 影響：僅 content-analyser。XSS 第二道防線補滿（DOMPurify 被繞過時，注入的 inline script 仍會被 CSP 拒絕執行）。

## 2026-06-21 重構：抽取共用 json_utils，去除 4 份重複 LLM-JSON 清理（已部署 00053）
技術債暫緩清單第一項（先補測試再去重）。
- 新增 `analysis-service/json_utils.py`：`clean_json_str`（去 fence + 抽最外層 {...}，含 None 防護）/ `parse_json_obj`。
- llm_path / synthesis / denoise `_clean_json`、image_report `_parse_vision_json` 全委派之；函式名與呼叫點不變，行為等價（對 None 更安全）。
- 先補 `tests/test_json_utils.py`（14 例 characterization）鎖行為再去重；36 tests 全過。僅 analysis-pipeline。

## 2026-06-20 補強：全系統 code review 後技術債清理 A–D（已部署）
多代理審查四服務 + 文件後，依序修正並部署（分支 chore/tech-debt-cleanup → main）。
- **A 安全修正（crawler 00075 / analysis 00052 / search-extent 00007）**：
  - deploy.sh content-analyser 補 ORIGIN_VERIFY_TOKEN secret + ENFORCE_ORIGIN_TOKEN=1（否則下次部署來源鎖定守衛靜默失效）。
  - SSRF redirect 繞過（跨 3 服務）：新增 crawler `net_guard.safe_urlopen` 逐跳重驗、analysis image_report / search-extent `_resolve` 同樣逐跳驗 IP，擋 redirect→metadata。
  - analysis job 加 owner 欄，外部金鑰只能查自己的 job（系統金鑰放行）；Path 1 逾時不再讀 daemon thread 半成品。
  - crawler 移除 log 印金鑰末 4 碼、og_meta 讀取上限；search-extent ThreadPoolExecutor 部分失敗不中止整批。
- **B 文件同步**：附錄 D / DEPLOY_CHECKLIST / SECURITY_INCIDENTS / MAINTENANCE 補來源鎖定與 7 把 secret；product_guideline 三→四服務 + search-extent 章節；§9.2「爬蟲不碰 Firestore」矛盾修正。
- **C 首批自動化測試**（先前零測試）：`tests/`（無需 pytest 可直跑）——SSRF 守衛 15 例 + 定價 7 例，全過。巨檔拆分/JSON 去重屬維護性、零測試線上系統回歸風險>價值，暫緩。
- **D 加固（search-extent 00008 / content-analyser 00070）**：移除 /profile 明文金鑰殘留（Phase 0，無處使用）、admin_usage/term_filters 加查詢上限、search-extent credential 快取 + angles 封頂。CSP unsafe-inline 移除需動 18 模板，暫緩。
- **驗證**：四服務 health 全綠；來源鎖定 image-only 部署後仍強制（run.app=403、CF=302、WAF /phpinfo=403）。

## 2026-06-20 新增：Cloudflare 資安防護 + 來源鎖定（方案 B，已部署）
為正式網域 insightout.annexix.cc 掛上 Cloudflare 防護，並封住 *.run.app 直打繞過。
- **content-analyser 來源鎖定守衛**：`app/__init__.py` before_request 驗 `X-Origin-Token`（Cloudflare 注入）。
  分段旗標：未設密鑰→停用；設了但 `ENFORCE_ORIGIN_TOKEN`≠1→軟模式（只記 log）；=1→缺/錯標頭 403。
  密鑰 `ORIGIN_VERIFY_TOKEN`（Secret Manager，與 Cloudflare Transform Rule 注入值一致）。
- **Cloudflare（annexix.cc，Free 方案，經 REST API 設定）**：
  - insightout.annexix.cc 開橘雲代理（proxied=true）→ 自動 DDoS、隱藏來源 IP、邊緣 TLS、免費受管規則。
  - Transform Rule（http_request_late_transform）：對 `http.host eq insightout.annexix.cc` 注入 `X-Origin-Token`。
  - SSL 模式 Full（原已是）；Bot Fight Mode 待手動開（臨時 token 無 Bot Management 權限）。
  - 自訂 WAF 規則（http_request_firewall_custom，附加於既有「非台 IP managed_challenge」之後）：block 常見漏洞/掃描路徑（.php/.env/.git/.sql/.bak/.ini/parameters.yml/phpinfo/phpmyadmin/wp-/.aws/.ssh），邊緣即擋、不耗 Cloud Run。實測 /phpinfo.php /wp-login.php /.env=403、首頁=302。
- **上線序列（軟→強，可秒退）**：守衛軟模式部署(00067)→ Cloudflare 設定 → 刷新載 token v2(00068)
  → 驗證注入（cf-probe 無「缺/錯」log、direct-probe 有）→ `ENFORCE_ORIGIN_TOKEN=1`(00069)。
- **驗證**：run.app 直連=403、insightout 經 CF=302。log 證實掃描器正直打 run.app（/phpinfo.php、/.env.js…），現已被擋。
- 影響：僅 content-analyser；content-crawler / analysis-pipeline 仍 ingress=all + 各自 X-API-Key，外部 Colab 不受影響。

## 2026-06-20 修正：報告 §3.2 情感/好感度消失（Cloud NL 逾時被降級）（未部署）
循環扇最新報告好感度沒出來、編號 3.1→4 中間缺 §3.2。根因：Cloud NL（run_entities_sentiment）
**逐篇序列**跑（最多 25 篇 × 2 次 API：實體+情感），合併分析篇數多時 >120s NL_DEADLINE → 被降級略過
→ enabled=False → `_section_entities` 回空字串 → §3.2 整段不渲染（好感度、實體都沒了）。
- 修：NL 逐篇改 **ThreadPoolExecutor 平行（8 workers）**，25 篇從 ~100–150s 降到 ~10–15s，穩在 120s 內。
- 順帶：單篇 API 失敗不再整段中止（原本第一個錯誤就 return enabled=False）；全失敗才降級。
- py_compile 通過（本地無 google-cloud-language，平行邏輯對齊已驗證的 grounding 平行化）。僅 analysis-pipeline。
- 註：該報告 §3.1 仍有 shopping/文章 等 URL 雜訊＝URL 修正部署前所跑；重跑即乾淨。

## 2026-06-19 新增：品牌聲量探勘（search-extent 子功能 D）（未部署）
回答「某品牌在某主題有沒有 earned 聲量」的缺席洞察（源於 GQ Shop 賣循環扇卻搜不到、報告零影響力）。
- **search-extent `brand_presence.py` + `POST /api/brand-presence`**：主題 × 品牌清單，對每品牌一次品牌錨定 grounding（系統 SA），請 Gemini 判定第三方聲量等級（有聲量/僅自有/缺席）+ 列依據來源；來源解析真實 URL、分自有 vs 第三方；依 earned_count 排 share-of-voice。品牌平行（max_workers 8）、grounding timeout 70s。token 記帳 system_token_usage(job_kind=brand-presence)。health 加 brand_presence_configured。
- **content-analyser**：`search_extent_client.brand_presence` + 專案頁「📊 品牌聲量探勘」摺疊面板（主題+品牌清單→等級表，每行一品牌）+ 持久化 `brand_scans` + 刪除路由。
- **驗證**：GQ Shop=缺席、Vornado=有聲量(earned 14)、小米=有聲量(earned 9)，21.8s/3 品牌——對齊真實觀察。py_compile + Jinja + 標籤平衡通過。
- 文件：README 子功能 D、附錄 B 端點、附錄 C brand_scans。分支 feat/brand-presence，未部署。

## 2026-06-19 修正：URL 碎片污染關鍵字（https/ptt/cc…）+ 建議引擎英文白名單過寬（未部署）
循環扇分析報告 TF-IDF 出現 https/ptt/cc/bbs 等無法濾掉的雜訊。根因二：
- **URL 沒清就斷詞**：一條 `https://www.ptt.cc/bbs/...html` 被 jieba 切成 https/www/ptt/cc/bbs/html… 一堆垃圾 token。修：`_text_for_keywords` 斷詞前 `re.sub(https?://\S+|www\.\S+)` 清 URL（全來源）。
- **內建停用詞補 URL/平台碎片**（地板）：http/https/www/com/net/org/html/htm/ptt/bbs/php，擋裸詞提及。
- **建議引擎（全庫學習）抓不到英文垃圾**：`_BRAND_POS` 含 'eng' → 所有英文 token（含 http/https/ptt 與品牌 Vornado/IRIS）一律白名單排除。改：移除 'eng'，真品牌改靠既有 Cloud NL salience 白名單保護（高 salience 實體），讓非實體的英文垃圾能被建議。
- 保留：`dc`(DC馬達=規格)、iris/vornado/acerpure(品牌) 不誤濾。
- 驗證：PTT 樣本斷詞後無 https/www/ptt/cc/bbs/html、保留 dc/循環/馬達；py_compile 通過。僅 analysis-pipeline，未部署。

## 2026-06-19 改善：內容來源區介面（流程說明＋關鍵字推薦收合＋上傳資料支援檔案/文字）（未部署）
依使用者要求調整「內容來源」UX；一般人用 doc/txt 不用 JSON。
- **流程說明**：專案頁最上方加說明卡「① 內容來源 → ② 資料集 → ③ 分析 → ④ 報告」，一進來就懂。
- **三方式明說**：內容來源標題下列「🔍 關鍵字推薦／📋 貼上網址／📥 上傳資料」三選一。
- **關鍵字推薦**改可摺疊、**預設收合**（屬選用，不占版面）。
- **上傳資料**（原「手動匯入」正名）：三模式擇一——**上傳檔案**（`.txt/.md/.docx` 多檔，每檔一篇、標題＝檔名）／**貼上文字**（標題＋textarea，一篇）／進階 JSON（收合）。後端 `create_manual_dataset` 重寫為三模式（優先序 檔案＞文字＞JSON），`_extract_doc_text` 解析（txt/md 直讀、docx 用 python-docx、.doc 擋並提示另存）。`requirements.txt` 加回 python-docx==1.1.2。
- 附錄 F 術語「手動匯入」正名為「上傳資料」。
- 驗證：py_compile + Jinja + 標籤平衡 + collapse id 唯一；`_extract_doc_text`（txt/.doc/未知）單元測試過。content-analyser 單一服務，未部署。

## 2026-06-19 改善：專案頁 IA 重組 + 文案標準化（依附錄 F，未部署）
把混亂的 ⓪①②+無編號混排，收成附錄 F 的四階段主線；純前端＋文案，邏輯/路由/資料不動。
- **重組四區**：① 內容來源（關鍵字推薦／貼上網址／手動匯入三入口**集中一區**，手動匯入由頁尾上移）→ ② 資料集 → ③ 分析（資料集列表＋送出分析）→ ④ 報告（歷史分析）。
- **文案統一**：推薦筆記→**推薦清單**；建立網址清單/建立新草稿/建立清單→**建立草稿資料集**；② 一鍵分析/合併分析鈕→**送出分析（勾選的）**；⓪ 關鍵字自動推薦爬取清單→**關鍵字推薦**；flash 訊息（草稿清單→草稿資料集）一併對齊。
- 取消並列入口各自編號（編號只給主線四階段）。
- 驗證：py_compile + Jinja；manualImport id 去重（移卡後）、if/for 標籤平衡、區塊順序符合 F.4。content-analyser 單一服務，未部署。

## 2026-06-19 文件：訂定產品術語與使用流程標準（CLAUDE.md 附錄 F）
專案 UI 因疊加功能而混亂（⓪①②與無編號混排、同動作多種講法、清單/草稿/推薦筆記/資料集詞彙重疊）。先訂規範再重組 UI。
- 主線心智模型：① 內容來源 → ② 資料集 → ③ 分析 → ④ 報告。
- 術語表（一詞一義、列禁用混用字）：內容來源/關鍵字推薦/推薦清單/貼上網址/手動匯入/資料集(狀態:草稿→爬取中→完成)/分析/合併分析/報告/延伸報告。
- 統一動作文案（送出分析/建立草稿資料集/開始爬取…）。
- 頁面分區標準（內容來源｜資料集｜分析與報告｜側欄）。
- 下一步：依此重組專案頁 UI + 統一文案（純前端，待核准改善）。

## 2026-06-19 新增：推薦筆記（⓪ 內容發現持久化 + 建新/併入現有草稿）（未部署）
延伸 ⓪ 關鍵字自動推薦：把推薦結果存成「筆記」可累積、勾選後建新草稿或併入現有草稿（使用者要求「草稿像筆記、有好幾個、勾選多個後才進爬蟲」）。
- **持久化**：`discover_urls` 改 POST，結果存 `projects/{pid}/discoveries/{id}`（query/candidates/count/by_source）。專案頁 `project_detail` 載入並渲染「🗒 推薦筆記」清單，重整不消失、可回來繼續勾。
- **建新 / 併入**：`discovery_to_draft` 路由——勾選的 URL 依 `mode`：`new` 建新草稿、`append` 併入選定現有草稿（`_append_urls_to_draft` 去重 append pending items + 更新 source_urls/計數）。可跨關鍵字累積成多個草稿筆記。
- **刪除**：`delete_discovery` 路由（🗑）。
- **UI**：⓪ 卡改「產生推薦筆記」（POST + reload）；每則筆記候選預設勾文章頁（列表/首頁不勾）、來源色標籤、建新草稿名稱 + 併入現有草稿下拉。
- 修掉 form 兩個同名 `target` 衝突 → 改 `mode`(new/append) + `existing_did`。
- 驗證：py_compile + Jinja。附錄 C 新增 discoveries 子集合。分支待建、未部署。

## 2026-06-19 新增：search-extent 重定義為「搜尋情報層」+ 內容發現（爬蟲前置第 0 階，未部署）
重新定義 search-extent 的模組目的、邊界、功能歸屬（章程寫進 README + CLAUDE.md 附錄 B）。
- **重定義**：從「需求側情報」升格為**搜尋情報層**——爬取之前提供主題的「需求（搜什麼）+ 供給（什麼在贏）」。唯讀、無狀態、單向、不爬不分析、只回情報清單。管線第 0 階：search-extent →(URL)→ 草稿 → crawler → analysis。
- **子功能歸屬**：A 需求側·關鍵字延伸（`/api/expand`，Ads，**卡 token 未完成不啟動**）；B 供給側·內容發現（`/api/discover`，**新增可用**）；C 趨勢層（規劃）。各自端點/資料源/開關，`/health` 各有 `*_configured`。
- **B 內容發現**（`search-extent/discover.py` + `/api/discover`）：關鍵字 → Vertex Gemini + Google Search grounding（**在 Google 端執行、非直爬 → 無 IP/CAPTCHA 問題**，系統 SA、不需 key/CSE）→ 多角度查詢 + 平行解析 vertexaisearch 轉址 → 標 source_type/region/flag、TW 優先。grounding 系統付 token → `system_token_usage`（service=search-extent）。
- **content-analyser**：新增 `app/search_extent_client.py`；專案頁「⓪ 關鍵字自動推薦」UI（關鍵字 → 候選勾選，列表頁/首頁預設不勾 → 帶入 create_dataset 建草稿）；`project_routes.discover_urls` 路由。
- **deploy.sh**：content-analyser 注入 `SEARCH_EXTENT_SERVICE_URL`（取自 search-extent）+ `SEARCH_EXTENT_API_KEY`。
- 驗證：py_compile 全 + Jinja + bash -n；`discover()` 本地端到端（循環扇 12 條全 TW、分類乾淨、token 計量、100.com.tw 因 SSRF 修復回歸）；留出驗證（循環扇/保時捷/CHANEL）。分支 `feat/search-extent-discover`，未部署。
- ⚠️ **A（Ads）依使用者指示維持不啟動**；標註未完成。

## 2026-06-19 新增：字詞過濾編輯區（可後台增刪、依來源 scope）+ 過濾建議分析（未部署）
背景：垃圾詞寫死在 nlp_path，越來越多要處理；且同詞跨來源語意不同（「編輯/積分/編號/此人」在論壇是版面文字，在媒體是內容）。先用循環扇真實語料驗證概念再開發。
- **F1 編輯區（content-analyser `/admin/terms`）**：後台 textarea 編輯 `詞 | 範圍 | media`，寫 Firestore `system/config.term_filters`。範圍多選（全部/媒體/社群/論壇/影音/電商）。
- **nlp_path 存取器化**：`_STOPWORDS` 等寫死清單改成 `get_term_filters()`（內建地板 + Firestore 合併，60s 快取，照 crawler `get_ad_blocklist` 模式）。新增 `_source_type(url)`。`全部` scope 走 `_tokenize` 統一過濾；其餘 scope 在 `_text_for_keywords` 依該篇來源逐篇 strip（推廣自既有 `_strip_social_ui`）。媒體名動態 `jieba.add_word`。**只增不減**（內建核心不可刪）。
- **F2 建議分析（analysis-pipeline `POST /api/suggest-filters` 同步 + 後台勾選 UI）**：三信號「跨來源歧異 × 同頁重複次數 × 詞性」找候選垃圾詞，自動建議 scope；品牌/英文/專名白名單保護；排除已在清單者。候選需人工勾選才寫入。
- **Cloud NL salience 白名單**：留出驗證（保時捷 49 篇含影音）發現影音逐字稿把領域詞（鋁合金/結構/材質）重複講 → 被誤判 chrome。`suggest_filters` 加 `_salient_entity_names()`：對語料跑 Cloud NL，高 salience 實體（領域詞/品牌）一律保護排除（best-effort + 30s 時限，NL 未啟用/逾時則降級為純 jieba，不致命）。CHANEL 33 篇全媒體 → 0 候選（精度安全，不誤殺領域詞）。
- **全庫學習（content-analyser `/admin/terms/suggest-all`）**：一鍵聚合『所有專案』爬蟲文本（上限 400 篇）跑 suggest_filters，發掘全站候選垃圾詞。比單一專案準（跨來源歧異有更多論壇/影音樣本）。後台「🧠 全庫學習」按鈕為主、單一專案分析為輔，共用候選勾選表。驗證：103 篇跨 3 主題合併，積分/編號/此人/文章/個人 強信號浮出。
- **概念驗證（循環扇 21 篇）**：積分/編號/此人/文章（論壇 chrome，同頁重複 6–12×）、觀看/總代理（影音）、不管/每天/之前/的話（填充）全精準浮出；品牌（Vornado/小米/Acerpure）正確白名單排除。
- **測試抓到 2 個真 bug 並修**：① `_source_type` 的 `'x.com'` 子字串誤中 winrex.com → 改精確 `//x.com/`；② 單篇來源 rate=1.0 灌爆候選 → 加 `df≥2 且該來源≥2 篇` 小樣本防呆。
- 驗證：py_compile×全 + Jinja×2；空 Firestore 回退 == 內建（向後相容）；論壇文本去 回覆/樓主、留 編輯/馬達；`suggest_filters` 循環扇端到端、`_parse/_serialize` round-trip 全過。分支 `feat/term-filter-editor`，未部署。

## 2026-06-19 修正：SSRF 封鎖橫幅卡死（永遠無法關閉）（已部署 deploy-20260619-3）
問題：被 SSRF 安全過濾擋下的 URL 存進 dataset `blocked`/`n_blocked` 後**只寫不清** → 即使該 URL 已修好、重爬成功，橫幅仍永遠掛著、無關閉鈕、且被擋 URL 不是 item 不在列表（使用者回報「沒解決、無法關閉、不在列表」）。僅 content-analyser。
- `recrawl_dataset` 啟動重爬時將 `blocked`/`n_blocked` 設 `DELETE_FIELD`（被擋 URL 本就落在 failed/all 重爬範圍）→ 橫幅立即消失。
- `_sync_crawling_dataset` 完成時**權威重寫** `blocked`/`n_blocked` 為最新 job 結果（沒有就歸零）→ 仍真被擋者誠實重現、已修好者清掉。
- `dataset_detail.html` 橫幅補 CTA：指向「🔄 重爬未完成/失敗項」，並說明成功後提示自動消失。
- 搭配同日 SSRF 6to4 修正：100.com.tw 重爬即通過 → 變列表中的成功 item，橫幅清除。
- 驗證：py_compile + Jinja parse 通過。分支 `fix/clear-blocked-banner`，已部署 deploy-20260619-3。

## 2026-06-19 修正：SSRF 6to4 AAAA 誤殺 + neterror 靜態救援/封鎖判別（已部署 deploy-20260619-2）
背景：診斷康泰「爬取失敗測試」資料集兩筆失敗，發現一筆其實是自家 bug。
- **100.com.tw（SSRF 誤判，真 bug）**：DNS 有合法公網 A（203.69.66.6）＋一個 6to4 AAAA（2002::cb45:4206）。`crawler-service/app.py` 的 SSRF 守門「任一解析 IP 危險就整站擋」，而 **Python 3.11 對 6to4(2002::/16) 全域位址誤判 `is_reserved=True`** → 整站被自家擋（先前「100.com.tw 禁止爬取」之謎，這層是自我封鎖）。修法：守門改「**IPv4 嚴格 / IPv6 縱深**」——IPv4 是 Cloud Run 實際出口，任一危險即拒；IPv6 不再以 `is_reserved` 過度封鎖全域/6to4 位址，但仍解出 6to4/Teredo/v4-mapped 內嵌 v4 擋住包私有位址的繞過（`2002:a9fe:a9fe::`→169.254.169.254 metadata、`2002:0a00::`→10.x 等仍擋）。
- **myfone.blog（資料中心 IP 封鎖，非暫時性）**：log 顯示 headless Chrome 一秒內 neterror（research 流程曾成功 398 字，之後每次都 neterror）；本機住宅 IP curl 得 200。判定為 WordPress.com/Automattic 在連線層級封鎖 Cloud Run 機房 IP，重爬無益。新增 `crawler.py:_neterror_salvage`：neterror 後以**同容器靜態 HTTP** 再試 → 抽得到內文就直接採用（Chrome 被擋但站台可達）；連線層級失敗則判機房 IP 被封 → 標 `cloaked/needs_manual`（🚫需手動），不再當可重試 failure 無限重爬。
- 驗證：py_compile×3 + bash -n；SSRF 單元測試（100.com.tw 解封、metadata/私有/6to4 包私有/loopback 仍擋、公網 v6 放行、localhost 擋）全過；`_is_safe_url` 對 100.com.tw 真 DNS 回 (True,'')。分支 `fix/ssrf-6to4-neterror-salvage`（與下方 token 修正一併部署），已部署 deploy-20260619-2。

## 2026-06-19 修正：Token 價格預估 + Cache-Control 根治舊版 + 內容偏少提示（已部署 deploy-20260619-2）
僅 content-analyser，單一服務一次部署。
- **Token 價格預估**：新增 `app/pricing.py`（2026-06-19 查得最新單價：gemini-2.5-flash 0.30/2.50、flash-lite 0.10/0.40、pro 1.25/10、claude opus 5/25、sonnet 3/15、haiku 1/5；embedding $0.025/1M chars）。模型名子字串比對（flash-lite 不誤判 flash）。analysis_detail 用戶付 token 旁顯示估算 $（含延伸報告）；`/admin/usage` 系統付改用此模組（**修正 embedding 單價 0.20→0.025，差約 8 倍**）。est_cost/est_embed_cost 註冊為 Jinja global。
- **Cache-Control 根治舊版**：`_security_headers` 對 HTML 回應加 `Cache-Control: no-cache, must-revalidate`（不動 /static）→ 部署後不再看到舊版 UI。
- **內容偏少提示**：dataset success 但字數 <500 的項標「⚠️ 內容偏少，可能不完整」；recrawl mode=failed 把 <500 字 success 項也納入重爬目標（補反爬偵測未涵蓋的 under-extraction）。
- 驗證：py_compile + Jinja×3 + pricing 單元測試（單價/不誤判/embedding）全過。分支 `fix/token-pricing-cache-shortflag`，已部署 deploy-20260619-2。

## 2026-06-19 新增：反爬偵測（cloaking 跨站漂移 + 封鎖頁）→ 標記需手動爬取（已部署 deploy-20260619-2）
背景：部分站對機房 IP/爬蟲特徵反爬——cool-style 捲動時 cloaking 漂移到 Mobile01（存到 219 字垃圾、還污染學選擇器）；100.com.tw 對爬蟲味請求回 403「禁止爬取」封鎖頁（>150字漏抓被當文章）。實測：住宅 IP/瀏覽器 UA 拿到真文章，爬蟲味 UA 被擋。
- **② 偵測（crawler `crawler.py`）**：
  - 跨站漂移：捲動後 `final_url` 可註冊網域 ≠ 目標 → 疑 cloaking（`_reg_host` 比對；同站 http/https 不誤判）。
  - 封鎖頁：`_looks_like_block_page`（強特徵「禁止爬取/驗證您是人類/Just a moment…」+ 內容<1200字才判，長文偶提不誤殺）。
  - 命中 → 回 `status='skipped'` + `cloaked/needs_manual` + error「…需手動爬取」，**不把誤導/封鎖內容當文章**。
  - **不學污染選擇器**：頁面跨站漂移時不把該選擇器存回原網域（修 coolsis 學到 mobile01 `#first-article` 的污染）。
- **① 住宅代理破解（dormant）**：`tiered_fallback.needs_upgrade` 對 `cloaked` 回 True → Tier3 開啟（`system/config.tier3_enabled`）時自動改住宅代理重抓；**目前 tier3 關閉**，故偵測後直接標記需手動（住宅那段先 dormant，使用者決定先不開）。
- **C 標記（analyser）**：dataset 該項顯示「🚫 需手動」+「⚠️ 反爬封鎖/飄移誤導 — 需手動爬取（請用手動匯入貼真人版）」。
- 驗證：crawler py_compile + Jinja + `_looks_like_block_page`/`_reg_host` 單元測試（封鎖頁/長文不誤殺/跨站/同站不誤判）全過。分支 `feat/anti-bot-detect`，已部署 deploy-20260619-2。

## 2026-06-18 修正：佇列模式逐篇進度/錯誤即時顯示（補回遷移時掉的回饋，已部署 deploy-20260619-2）
問題：佇列 worker `run_crawl_chunk` 只寫結果、不逐篇更新 job log/progress → 資料集頁整塊跑完前停在 0%、看不到錯誤（背景模式 `run_crawl_batch` 本有逐篇更新）。
- `run_crawl_chunk.record`：逐篇更新 job `log = "(全域序號/總數) 狀態：標題"`，失敗/略過**附 error**；依全域序號估算 `progress`（讀一次 job.total）。
- chunk worker 例外 → 先寫 job log 再上拋（Cloud Tasks 重試），不再靜默。
- `run_crawl_batch.record` 也補上 error 顯示（與佇列一致）。
- 驗證：py_compile。分支 `feat/force-listing`（與 force_listing 一起部署）。

## 2026-06-18 新增：強制爬取列表/商品頁（force_listing，使用者勾選後重爬，已部署 deploy-20260619-2）
背景：分類/商品列表頁被 `_is_listing_page` 自動略過（status=skipped）；但商品導向頁對電商/品類研究有用。讓使用者判斷後**勾選開關強制重爬**抓回。
- **crawler**：`scrape(..., force_listing=False)`——列表頁 skip 點（crawler.py:2285）`force_listing=True` 時改強制抽取、不略過。沿既有 `use_gemini` 管線一路串接：`/api/crawl/batch` 讀 `force_listing` → 佇列 task payload / fallback thread → `run_crawl_chunk`/`run_crawl_batch` → `_crawl_sequence` → `_scrape_one`/`_proxied_scrape` → `scrape`。
- **content-analyser**：`submit_crawl_batch(..., force_listing=False)` 入 payload；`recrawl_dataset` 讀 `force_listing` 表單參數傳入（mode='failed' 本就把 skipped 當重爬目標 → 配 force_listing 把被略過的列表頁抓回）；dataset_detail「🔄 重爬未完成/失敗項」旁加勾選框「強制爬列表/商品頁」。
- 預設行為不變（不勾＝列表頁照舊略過、保乾淨）；不影響其餘爬取/分析邏輯。提醒：列表頁抽出內容較薄（商品名/短描述），適合品類/市場研究。
- 驗證：crawler+analyser py_compile、dataset_detail Jinja 解析；force_listing 全鏈路 22 處串接一致。分支 `feat/force-listing`，已部署 deploy-20260619-2。

## 2026-06-18 新增：草稿資料集（貼上=只建清單，按「開始爬取」才送爬蟲，已部署 deploy-20260619-2）
解：貼上網址只是保存使用者輸入的記憶，清單不因刪除/重載消失；爬取是獨立動作，與後續爬蟲流程完全解耦。僅 content-analyser。
- **`create_dataset`** 改為建立 `status='draft'` 資料集：存 URL（正規化去重）+ 寫 items（`status='pending'`），**不送爬蟲**。清單持久化、可逐筆刪除（A 的 🗑）、重載不消失。
- **新路由 `start_crawl`**（`POST /<pid>/datasets/<did>/crawl`）：按「▶ 開始爬取」才以「目前 items 清單」（使用者可能已刪部分）送 `submit_crawl_batch` → `draft → crawling`，之後完全照既有流程（爬完覆寫 pending → 結果；recrawl/auto-continue/research 全不受影響）。失敗保留草稿可重試。
- **UI**：dataset_detail 草稿狀態顯示「▶ 開始爬取」+「🗑 刪除草稿」、草稿提示橫幅、待爬清單（pending 顯「待爬」徽章、可逐筆刪）；專案頁「① 建立網址清單（草稿）」按鈕＝建立清單；列表 draft 顯「草稿」徽章。
- 解耦保證：草稿階段與 recrawl/auto-continue/research 零關聯（它們都在爬取之後才作用）。
- 驗證：py_compile + 兩模板 Jinja 解析通過。分支 `feat/draft-dataset`，已部署 deploy-20260619-2。

## 2026-06-18 補強：資料集 A — URL 正規化去重 + 單篇刪除（已部署 deploy-20260619-2）
僅 content-analyser。解「重複網址造成資料權重傾斜」+「無法刪單一網址」。
- **`_url_key()` 正規化去重鍵**：小寫 scheme+host、去預設 port/`#fragment`/尾斜線、剝已知追蹤參數（utm_*/fbclid/gclid…，保留其他 query 如 `?id=1`）。原始 URL 仍保留供爬取/顯示。
- **`parse_url_list`** 改以 `_url_key` 去重（爬取建集）；**`create_manual_dataset`** 加資料集內去重（同 URL 只留一筆，flash 顯示去重數）；items 寫入時存 `url_key`。→ **一個資料集內一個 URL 只出現一次**。
- **單篇刪除**：新路由 `POST /<pid>/datasets/<did>/items/<item_id>/delete`（Owner/Editor，重算 item_count/succeeded）；`_load_dataset_items` 補 `_id`；`dataset_detail.html` 每篇加 🗑 刪除鈕。
- 驗證：py_compile、Jinja 解析、`_url_key` 7 案例單元測試（含「不誤併不同 path/有意義 query」）全過。分支 `feat/dataset-dedup-delete`，已部署 deploy-20260619-2。
- 待做 B（清單先持久化 + 結果合併不覆寫）另案。

## 2026-06-18 修正：SSRF 過濾改逐 URL 跳過（不再整批失敗）+ 顯示被擋原因（已部署 deploy-20260619-2）
問題：crawl/extract-images batch 端點原本「任一 URL 被 SSRF 過濾 → 整批 400 失敗」，且 content-analyser 只顯示「N 個 URL 被 SSRF 過濾拒絕」不顯示是哪個/為何 → 使用者無從修。
- **crawler `app.py`**：`/api/crawl/batch`、`/api/extract-images` 改為「只有**全部**被擋才 400；否則照爬安全的、把被擋的（url+reason）記在 job 文件 `blocked`/`n_blocked`」。`scrape/batch`（逐項）與 `research`（已 proceed-if-any-safe）原本就 OK。`get_crawl_job` 回傳整個 job 文件 → `blocked` 自動流到呼叫端。
- **content-analyser**：`_sync_crawling_dataset` 把 job 的 `blocked`/`n_blocked` 存到 dataset；`dataset_detail.html` 顯示「N 個 URL 因安全過濾未爬取」+ 逐筆 url/reason 清單。
- SSRF 過濾本身（`_is_safe_url`，C1）邏輯不變：擋非 http(s)/缺 host/metadata/DNS 解析不到/私有保留 IP。
- 驗證：crawler+analyser py_compile、dataset_detail Jinja 解析通過。分支 `fix/ssrf-per-url`，已部署 deploy-20260619-2。

## 2026-06-18 補強：選擇器 agent B-1（P1 耐用性+儀表化、P2 結構化資料優先，已部署 deploy-20260619-2）
降低人工 SITE_TEMPLATE 與重學頻率。僅 content-crawler。
- **P1a 選擇器正規化**（`site_learning.normalize_selector`，寫入收斂點 `save_learned_selector`）：`.ComponentName-aB3dEf`（styled-components PascalCase-雜湊）→ `[class*="ComponentName"]`；`.css-xxx`/`.sc-xxx`（框架原子類）與 `#post-12345`（數字 id）→ 拒絕（不可泛化）；剝除 `:nth-child`；乾淨 class/語意標籤原樣保留。`_looks_hashy` 防誤轉（`.Grid-container` 等單字不動）。讀取端 ≥300字/非列表/非cookie 仍兜底。
- **P1b resolved_by 儀表化**：`_extract_main_text` 每個 return 標 `last_resolved_by`（learned/template/structured/heuristic/llm/body_fallback/failed）；爬取結果 item 帶 `resolved_by`（隨 results 持久化）；每次爬取彙總寫 `crawl_telemetry/global`（by_method Increment）→ 觀察分布、指導 P3/P4。
- **P2 結構化資料優先**：`_extract_main_text` 在模板之後、通用啟發式之前插入 Phase 2.05——`_extract_from_json_ld`（既有，articleBody）/ `[itemprop="articleBody"]`，≥500 字才採用（避 teaser），標 structured。重用實戰抽取器，僅調順序+加閘門+標記。
- 驗證：crawler py_compile；`normalize_selector` 11 案例單元測試全過（含 `.Grid-container` 不誤轉）。分支 `feat/selector-robustness`。**P2 動 live 抽取順序，待部署後爬已知站（JSON-LD/雜湊/一般）回歸驗證**。P3（研究器接受邏輯）/P4（自癒重學）待 P1b 數據出來再評估。

## 2026-06-18 新增：全面 Token 用量記帳（待開發 10，已部署 deploy-20260619-2）
記錄產品執行時所有 LLM/embedding token 消耗。分流：**用戶付（專案 LLM Key）跟專案走、系統付（系統 SA）進管理者後台**。
- **共用 helper `analysis-service/token_usage.py`**：`norm_usage`（正規化 gemini/claude/openai usage）、`aggregate`（依 category 彙整）、`write_system_usage`（系統付 → `system_token_usage` collection，每 job 一筆 rollup）。
- **用戶付（→ 專案）**：`LLMClient` 內部 `_call_*` 改回 `(text, usage)`，`generate()/generate_vision()` 攔 `usage_metadata` 記入 `self.usage_log`，各呼叫點帶 `category`（synthesis_*/qualitative/search_intent/label_clusters/derived/combined/image_*）。四種 job（analysis/derived/combined/image）跑完彙整 `token_usage` 隨 job 狀態回傳；content-analyser 落地到 `analyses/{aid}.token_usage`（延伸報告存 `derive_token_usage`）+ 累加 `projects/{pid}.token_usage_total`（完成分支僅進一次，Increment 不重複）。analysis_detail/project_detail 顯示。
- **系統付（→ 後台）**：降噪（`denoise.py` 抓 gemini usage，accurate）+ embedding（`nlp_path._get_embeddings` 加 `usage_sink` 計實際送出字元，估算）+ KB 索引 embedding（`kb_index`）→ `write_system_usage`。`/admin/usage` 加系統付 Token 區塊（依 category/model 彙總 + embedding 字元 + 估算金額，單價表 `admin_routes._PRICE_PER_1M` 可調）。
- **相容**：全部攔截 best-effort（try/except），失敗只是該筆不記帳、不影響分析/爬取；新增參數皆 optional 預設不改行為；status 回應新增 `token_usage`（passthrough，向後相容）。
- **Phase 2b（爬蟲選擇器，已完成）**：`research._ask_gemini_for_selector` 加 `usage_sink` 抓 usage_metadata，`run_research` 彙整後 `_write_selector_usage` 經 `site_learning._client()` 寫 `system_token_usage`（service=content-crawler、category=selector_research，schema 與 analysis 端一致，後台統一彙整）。
- 強化（review #1）：pipeline 彙整改 `aggregate(list(llm.usage_log))` 快照，避免逾時殘留 daemon thread 併發 append。
- 驗證：三服務 `py_compile` + `bash -n` 通過；`norm_usage`/`aggregate`/`write_system_usage`/`_record_usage`/選擇器 doc 相容性 單元 + mock 整合測試通過。分支 `feat/token-accounting`（worktree）。

## 2026-06-17 修正：系統安全稽核 8 項修補（多 agent 稽核確認，已部署 deploy-20260617-1）
多 agent 安全稽核（7 區塊：prompt 注入/Web 注入/溢位 DoS 成本/授權/機密/Cloud Run 韌性/Firestore，對抗式驗證）確認 8 項真實可觸發弱點（無 critical/high；2 medium + 6 low）。逐項修補：
- **#1 CSV 公式注入（medium）** `report.py`：新增 `_csv_safe`，cell 以 `= + - @ \t \r` 開頭時前置單引號中和；`_to_csv` 對表頭與每格套用。擋不可信 keyword/entity/itemset 落入 CSV → Excel/Sheets 開啟求值（HYPERLINK/DDE）。數值權重為 float 不受影響。
- **#2 手動匯入 OOM（medium）** `app/__init__.py` + `project_routes.py`：設 `MAX_CONTENT_LENGTH=16MB`（擋超大 body 在解析階段吃滿 1Gi 記憶體；Werkzeug 3.0.x 預設無上限）；`create_manual_dataset` 在 `json.loads` 前加 12MB 早期長度防護（明確訊息而非 413）。
- **#3 label_clusters 未防注入（low）** `synthesis.py`：群關鍵字+標題（爬取衍生）改套 `INJECTION_GUARD` + `wrap_untrusted(tag=CLUSTERS)`，與 `run()` 一致；補上唯一漏網的注入點。
- **#4 白名單撤銷對既有 session 無效（low）** `auth_guards.py` + `__init__.py` + `project_routes.py`：新增 `refresh_whitelist_status()`（TTL 60s 回查 Firestore），`login_required` 與 `project_access_required` 皆改用，撤銷最遲 60s 生效；`PERMANENT_SESSION_LIFETIME=12h` + `before_request` 標記 session permanent（滑動到期）。dev 環境視為 approved 不回查。
- **#5 提交端點無速率/配額（low）** `analysis-service/auth.py` + `crawler-service/auth.py` + `deploy.sh`：外部 `api_keys` 路徑加每日配額（`_within_daily_quota`，預設 1000/日、UTC 日界重置、可於文件設 `daily_limit` 覆寫；超量回 False→401）；**系統金鑰（產品自用）走 compare_digest 提前放行、不受限**。`deploy.sh` 為 analysis-pipeline 與 content-analyser 補 `--max-instances 10`（爬蟲原已有），封頂 Cloud Run 成本爆量。
- **#6 denoise 無降噪總量上限（low）** `denoise.py`：`MAX_DENOISE_ARTICLES=30` 封頂每次分析降噪篇數（超出用原文、明確記錄被略過數，非靜默截斷），擋偽造 100 篇 YT/FB URL 燒系統 Vertex 配額。
- **#7 逾時後 daemon thread 續燒成本（low）** `llm_path.py` + `pipeline.py`：附加式 `should_stop` 回呼（預設 None 不改行為）；Path 2 逾時時排隊中的意圖批次/質化分析跳過後續 LLM 呼叫；Path 1 逾時時 search-extent 迴圈停止剩餘 Google Ads 呼叫。nlp embedding（系統成本、有 per-call timeout）未動以縮小風險面。
- **#8 Gemini 金鑰入 URL query（low）** `project_routes.py`：模型清單查詢改用 `x-goog-api-key` header（對齊 OpenAI/Claude 分支），避免網路例外字串夾帶含金鑰 URL 落入 log。
- 驗證：三服務 `py_compile` + `bash -n deploy.sh` 全過；`_csv_safe` 與 `_within_daily_quota` 單元邏輯測試通過。回捲點 tag `snapshot-20260617-pre-security-fix`。分支 `fix/security-audit`。
- Firestore：`api_keys` 文件新增選用欄位 `quota_day`/`quota_count`/`daily_limit`（見附錄 C）。無對外 API schema 變更。

## 2026-06-17 新增：登入頁產品行銷文案 + 專案頁方法論/使用說明（content-analyser 00040-jth）
- 登入頁（login.html）：landing 式行銷 hero，給初次接觸者——產品定位「讓內容團隊看見市場已驗證的有效方向」、三賣點（市場驗證/差異化切點/搜尋情境）、運作三步、白名單註記；保留 Google 登入卡。
- 專案頁（projects.html）：頂部加可收合面板——方法論（市場基準線/差異化切點）+ 四步開始 + 選材小建議。給已登入要開始用的人。
- 中英文（產品名英文、訴求中文）；純前端文案，不動後端/路由/權限。

## 2026-06-17 新增：逐字稿降噪前處理（A 抽取式降噪 + B 訊號抽取）（analysis-pipeline 00033-jhf）
口語/社群來源（YouTube/FB/IG/論壇）逐字稿雜訊（平台框架/CTA/業配/離題/口頭禪）嚴重干擾分析。進分析前先降噪。**降噪≠摘要：內容逐字保留，只移除非內容。**
- **denoise.py**：`is_spoken_source`（URL 判定）；`denoise_contents` 並行降噪。A `cleaned_text` 取代該篇 text 進 TF-IDF/分群/Path2（原文存 `_raw_text`）；B `signals{appeals,specs,objections,quotes}` 原話抽取 → 餵 synthesis §4/§5。
- **系統 Vertex SA（ADC，無金鑰）+ flash-lite**（gemini-2.5-flash-lite，低溫機械抽取，系統吸收成本）；**實測 SA 可呼叫 flash-lite 生成，不需 GENAI_API_KEY**。
- pipeline 前處理步（Path1/2 前）；synthesis 收 source_signals 注入。媒體文章不降噪；爬蟲/content-analyser 不動；主報告唯讀原則不變。
- 安全降級：失敗 / cleaned <80 字 → 退回原文，不擋分析；MIN_DENOISE_CHARS=800 免短篇。
- **實測（保時捷 46 篇，2 輪）**：CLE200 FB 3698→2389（圓夢敘事逐字留、FB 框架/業配/離題去）、超認真 1862→1165、Cayenne 5324→3499、IG 推廣 1070→302。全程分析完整跑完（~4.5 分）。
  - 測試發現並修正兩 bug：①大篇 cleaned_text 包 JSON 超 token 截斷 → 改 `response_schema` 結構化輸出 + max_output_tokens 16384；②`<30%` 安全閥誤退雜訊重貼文 → 改絕對 `<80 字` 才退回。

## 2026-06-17 新增：Phase 2 知識庫文件 RAG（解耦式：系統檢索、用戶 Key 生成，已部署）
每個專家可上傳參考文件，延伸報告生成時系統檢索最相關片段注入——對齊「檢索＝系統、生成＝用戶」分工。
- **analysis-pipeline**：`kb_index.py`（`chunk_text` ~600 字/重疊 80；`reindex_expert` 讀 documents→切塊→`_get_embeddings`(系統 SA Vertex)→`kb_chunks`，重建前清舊塊；`retrieve` query embedding + 記憶體 cosine top-K=5，任何錯誤→回 [] 降級純手冊）。`POST /api/kb/index {expert_slug}`。`audience_reports._build_one` 生成前先系統檢索注入「知識庫參考資料」；無文件/檢索失敗→純手冊不擋生成。
- **content-analyser**：`kb_store` documents 子集合 CRUD（存抽取純文字）；`admin_routes._extract_text`（md/txt/pdf via `pypdf`）+ 上傳/刪除/重新索引（上傳/刪除後自動觸發重建索引）；`admin_knowledge.html` 每專家文件管理；`requirements` 加 `pypdf==4.3.1`。
- 相容：無文件專家＝Phase 1 純手冊；`kb_chunks` 為新 collection（回捲不影響）。in-memory cosine 適小語料，大語料再升 Firestore 向量索引。

## 2026-06-17 新增：知識庫管理 + 延伸報告動態化（Phase 1，analysis 00030-d67 / analyser 00036-cnz）
延伸報告從寫死三 persona 升級為「後台知識庫管理的動態專家」（模型 A：啟用的專家＝報告頁可產生的延伸報告類型）。生成仍用**用戶專案 LLM Key**，系統不負擔生成成本。
- **後台 `/admin/knowledge`**：專家清單（啟用切換/排序/編輯/刪除）+ 建立（slug 不可改、顯示名、persona prompt、手冊 markdown）；首訪自動種子三專家（aeo/ecommerce/ads）+ 骨架方法論手冊（康泰打法待管理員補）；控制台加入口。
- **kb_seed.py / kb_store.py**：`kb_experts`（doc_id=slug）CRUD + 冪等 seed + enabled/order。
- **延伸報告動態化**：derive 觸發改撈「啟用專家」傳入 analysis-pipeline；存 `analyses/{aid}.derive_experts`(slug+label)；檢視/下載改動態 slug（`slug_ok` 驗證，非固定白名單）；報告頁依 derive_experts 動態列出。
- **audience_reports.py**：改依 payload `experts[]`（{slug,label,prompt,playbook}）逐一並行生成，手冊常駐注入、主報告唯讀，本服務不持有專家定義。`/api/audience-reports` payload 加 `experts[]`（必填）。
- 主報告 `result_markdown` 完全唯讀；`kb_experts` 為新 collection（回捲不影響舊功能）。回捲點 tag `snapshot-20260617-pre-knowledge-base`。
- **Phase 2（另案）**：文件上傳 + 索引（Vertex 系統 SA embedding → kb_chunks）+ 生成時系統檢索 chunks 注入（解耦式 RAG：系統檢索、用戶 Key 生成）。

## 2026-06-17 新增：三份延伸行動報告（AEO / 品類經理 / 投放師）（analysis 00029-qcj / analyser 00035-ngp）
主報告是分析員視角；新功能把它翻譯成三種角色的行動指引。**分析師在主報告完成並認可後手動按鈕觸發**，唯讀主報告、結果綁母分析（aid）；主報告換＝新 aid＝重產。
- **analysis-pipeline**：新增 `audience_reports.py`（mirror combined_report 模式，輕量、3 份並行 LLM）。三份各有 persona prompt，從主報告 markdown 對應段落摘取 + 轉成行動語言：
  - `aeo`：AEO 指引（核心情境題清單 / 答案型內容 / AI 易摘取格式 / 待答缺口）— 摘自 §4 搜尋情境 + §3.2 實體 + §7 缺口 + 附錄。
  - `ecommerce`：品類經理行銷指引（核心訴求 / 標題內文圖片 / 差異化 / 廣告切入）— 摘自 §5 質化 + §6 建議 + §3 分群 + §7 缺口。
  - `ads`：投放師優化建議（受眾分眾 / 文案切角 / 圖片素材 / 關鍵字切入）— 摘自 §4 情境 + §3 分群 + §2/§7 關鍵字 + §5 語言。
  - 單份失敗降級不影響其他兩份。app.py 新增 `POST/GET /api/audience-reports`（`audience_jobs/{job_id}`）。
- **content-analyser**：analysis_client 加 submit_audience/get_audience_status；project_routes 加 derive 觸發（editor）、derive/status 輪詢（完成存回 `analyses.derived_reports`）、檢視、下載 .md；analysis_detail 加產生按鈕 + 輪詢 + 3 份檢視/下載；新 `derived_report.html` 檢視頁（marked.js + DOMPurify）。
- **主報告 result_markdown 完全唯讀、未改動**；延伸報告生命週期綁母分析 aid。
- **實測（保時捷 46 篇報告延伸，00029-qcj）**：三份齊全、各 4 段、深度接地（引用 992.2/Taycan vs Model3/800V/德中台市場/客製化）；UI 6 顆鈕、view/download 端點皆 200；主報告未變。

## 2026-06-17 新增：三項數值分析 CSV 獨立下載（核實用）+ TF-IDF 25→50（analysis 00028-ggw / analyser 00034-g6w）
依使用者「報告中列出三個數值分析結果、編成獨立下載檔供核實；TF-IDF 放寬到 50」需求。
- **三項 CSV 匯出**（TF-IDF / 關聯規則 / Cloud NL 實體情感）：
  - analysis-pipeline `report.build_numeric_exports(nlp_results)` 產 3 份 CSV 字串（csv 模組正確跳脫）：
    `tfidf`=rank/keyword/weight；`association`=itemset+rule 同表（type 區分，support/confidence/lift/count）；
    `entities`=情感 meta（enabled/n_docs/avg_sentiment）+ 實體表（entity/type/salience/mentions）兩區塊堆疊。
  - `pipeline.py` 完成時存 `analysis_jobs/{job_id}.numeric_exports`（map）；`app.py` GET completed 回傳 numeric_exports（passthrough，client 自動帶過）。
  - content-analyser 輪詢完成存 `analyses/{aid}.numeric_exports`；新增路由 `GET /<pid>/analyses/<aid>/download/<kind>.csv`（kind 白名單 tfidf/association/entities，**utf-8-sig BOM 供 Excel 正確顯示中文**）；analysis_detail 加 3 顆 CSV 下載鈕（僅 numeric_exports 存在時顯示，舊報告相容）。
  - 報告內 §2/§3.1/§3.2 表格維持不變 → 人看報告、Excel 核實數值，並存。
- **TF-IDF TOP_KEYWORDS 25→50**（§2 表格同步）：關鍵字更完整。ASSOC_VOCAB（30）、Cloud NL ents（25）不變。
- **實測（保時捷 46 篇，00028-ggw）**：§2=50 列；numeric_exports 三鍵齊全（tfidf 50 列/association 35 列/entities meta+實體）；UI 3 顆鈕、三個下載端點皆 200 text/csv；快取 46/46 命中。

## 2026-06-16 補強：社群 UI 停用詞（依來源條件套用）+ Cloud NL 實體過濾媒體名（00027-jdv）
- **社群/論壇 UI 雜訊條件移除**：回覆/留言/回文/樓主/小編/轉發/推文/引用/私訊/鄉民/網友… **只在社群/論壇來源**去除，媒體站不動（媒體文章的「回覆」可能是內容）。因 dataset items 的 source_type 多半未填，改**依 URL 網域判定**（`_SOCIAL_DOMAINS`：facebook/instagram/threads/dcard/mobile01/ptt/巴哈/eyny…）。`_text_for_keywords` 只對社群來源 `_strip_social_ui`；embedding 仍用原始文本（快取 key 不受影響）。run_tfidf/run_association 共用。
- **§3.2 Cloud NL 實體過濾媒體名**：Cloud NL 獨立於 jieba 抽實體，「地球黃金線」等媒體名會以實體漏進 §3.2（先前 jieba 側過濾管不到）。run_entities_sentiment 丟掉名稱屬 _MEDIA_NAMES/_STOPWORDS 或被媒體名包含（碎片如「黃金」）的實體。
- **實測（保時捷 46 篇）**：§3.2 不再有「地球黃金線/黃金」；§2/§3.1 keyword 表「回覆/留言」歸零；媒體文章關鍵字不受影響；快取 46/46 命中。

## 2026-06-16 修正+新增：數值閘門誤判修正(A0) + embedding 內容快取(A3)（analysis-pipeline 00024-fqj）
線上問題：保時捷 46 篇分析「數值語意探勘失敗，已中止」。雙重根因 + 雙重修正。
- **A0 止血**（fix/numerical-mining-timeout）：
  - 根因：Vertex embedding 序列批次、單呼叫無逾時 → 46 篇耗 ~9 分鐘 > 600s Path 1 join；逾時又連同「已成功的 TF-IDF」一起丟棄 → 閘門誤判數值層失敗（TF-IDF 其實 4 秒就完成）。
  - `_get_embeddings` 改並行批次（EMBEDDING_WORKERS=4）+ 單呼叫 HTTP 逾時 60s + 重試。
  - `run()`：分群（CLUSTER_DEADLINE_SEC=240s）、Cloud NL（NL_DEADLINE_SEC=120s）各加硬時限，逾時即降級（分群→單群、NL→停用），run() 必在 ~6 分內返回。TF-IDF + 關聯（秒級本地）才是閘門核心。
  - pipeline：Path 1 逾時不再丟棄已完成數值結果，只清 search-extent；NL_MAX_DOCS 40→25。
  - **實測：embedding 46 篇 9 分鐘 → 52 秒。**
- **A3 embedding 內容快取**：Firestore `embeddings/{sha256(model:dim:text)}` 存向量；向量化前先 `get_all` 查、只對 miss 呼叫 Vertex、再 `batch()` 寫回。key 含 model+dim → 換模型自動失效。完全 fail-safe（任何快取錯誤/數量不符 → 退回純向量化）。模型名抽成 `EMBED_MODEL`/`EMBED_DIM` 常數（仍用 002）。
- **§3.1 關聯規則治本**：原以每篇 TF-IDF top 詞為品項 → 取到「獨特詞」、主題核心詞（品牌/車系 IDF 低）反被排除 → 46 篇回 0 條。改用**語料級 Top 30 關鍵字為品項詞彙**，各篇籃子＝它實際含有的那些（`_article_terms` 做 unigram+bigram，與 TfidfVectorizer ngram(1,2) 同口徑），support 0.15→0.10。實測 0 條 → **20 組共現 + 15 條規則**（電動車↔市場 lift 1.46、賽道↔設計 1.47、德國↔電動車 1.44 等）。
- **決策**：embedding 模型暫不換（gemini-embedding-001 單價 6× 但每次 <1¢，品質中等提升，先看結果再決定）；Vertex Batch Prediction 對 N=20–200 不值得，否決。
- **實測驗證（保時捷 46 篇，2026-06-16）**：總分析 ~3m45s（原整批失敗）；二次跑 embedding 快取 46/46 命中、Path 1b 4 秒；§3.2 Cloud NL 25 篇/25 實體、§1 摘要直接引用 salience 數值。
- ✅ 雜訊清理（00026-hr6）：擴充 `_STOPWORDS`（我們/提供/推出/表示/透過/全新…通用填充+公關動詞）+ 新增 `_MEDIA_NAMES`（地球黃金線/車訊網…當複合詞 add_word 整塊切出再停用，只去品牌、保留「黃金」一般用法）。實測：§2 TF-IDF Top25 與 §3.1 itemsets/rules **keyword 表雜訊歸零**；快取 46/46 命中（embedding key 用原始文字，不受斷詞影響）；§3.1 規則更乾淨（系統↔設計/性能、電動車↔市場/性能、交付→市場/新車/台灣）。LLM 描述與 Cloud NL 實體不受影響（刻意保留自然語言）。


## 2026-06-16 新增：數值語意探勘層（關聯規則 + Cloud NL）+ 數值閘門（analysis-pipeline 00023-n7d / v1.2.0）
依使用者「TF-IDF 和這邊的，作為數值分析的語意探勘，各自找到數值結果，再透過 LLM 解讀解釋，最後加 LLM 延伸」需求，把數值層擴充為四項並加閘門。已部署上線（feat/numerical-mining → main）。
- **Path 1c 關聯規則探勘（新增，nlp_path.run_association）**：以各篇 top 關鍵字為「交易品項」，純本地計算頻繁共現組合 + 關聯規則（support/confidence/lift），毫秒級、零外部套件。對應方法論一（高 support 有效組合）＋方法論二（高 lift 強關聯切角）。門檻：每篇取前 8 詞、min_support 0.15、min_conf 0.5、最多 15 條。
- **Path 1d Cloud Natural Language（新增，nlp_path.run_entities_sentiment）**：`analyze_entities`（salience）+ `analyze_sentiment`（整體情感），最多 40 篇。未啟用 `language.googleapis.com` / 未裝套件 → `enabled=False` 優雅降級不擋報告。（API 已啟用，立即生效。）
- **數值閘門（pipeline.py 改序列）**：Path 1 先行，TF-IDF 核心無結果即視為數值探勘失敗 → 直接 `failed`、**不進入 LLM**（不燒 token 產無依據報告）；分群/關聯/實體可降級不擋。Path 1 通過閘門後才跑 Path 2（LLM 質化）。修掉原並行架構在 Path1 逾時時仍照跑 LLM 的問題。
- **Vertex embedding 強化**：`EMBEDDING_BATCH` 5→20 + 3 次重試（退避），修正先前 8 篇分群耗時 13 分鐘 > 600s join 逾時 → 整個 Path1 成功結果被丟棄的根因（保時捷 §2/§3 空白）。
- **synthesis.py**：關聯規則 + 實體/情感整理成 prompt 區塊注入共用 context，§1/§4/§6/§7 章節據此解讀數值。
- **report.py**：新增 §3.1 主題關聯規則、§3.2 關鍵實體與情感（程式直生，無資料則略過）。依需求**語意群與附錄維持只列標題 + 超連結，不貼全文**——文本僅作必要佐證，結論與洞察為主。
- **requirements**：新增 `google-cloud-language>=2.13.0`。

## 2026-06-16 修正：ETtoday 等 SSR 站靜態抽取（避開 Chrome 149 CDP 崩潰，已部署（隨批））
ETtoday 無法爬取根因：站為 SSR（內文在靜態 HTML 的 `.story`），但爬蟲因「已知站」跳過 SSR 預探測、硬開 Chrome，而 Chrome 149 在其廣告/JS 重頁觸發 CDP bug `missing or invalid columnNumber` → driver 崩潰。修正（crawler.py，重用 `_extract_main_text`）：
- 新增 `_static_extract(url)`：抓靜態 HTML 跑模板/啟發式抽正文，足量（≥400 字）即回（source=`static_template`）。
- **prefer-static**：`PREFER_STATIC_DOMAINS`（ettoday.net）先試靜態、成功跳過 Chrome（proactive 避崩、更快）。
- **Chrome 崩潰/例外後備**：scrape 的 `WebDriverException`/`Exception` 分支先試 `_static_extract` 救回（通用安全網，任何 SSR 站在 Chrome 死掉時可救）。
- 本機驗證：speed.ettoday.net → 命中 ettoday 模板 `.story`、1144 字、未開 Chrome。

## 2026-06-16 新增：卡住任務收割機制（job reaper，已部署，feature branch feat/job-reaper）
解決 job 卡在非終態（pending/queued/running）但 worker 已死 → 變孤兒、永不結束的問題（今天清掉 5 個 crawl_jobs 孤兒後加此機制防再發）。**全自動、零外部排程**：
- **crawler / analysis 各加 `reaper.py`**：`reap_stale(db, collections, 60min)` 把「非終態且超過 60 分無更新」的 job 標 `failed`（reaped），再由既有「逾 N 天刪除」cleanup 回收。閾值 60 分 > 批次 45min / 單塊 25min。
- **reap-on-submit（自動化③）**：每個提交端點（crawl/extract-images/research、analyse/analyse-images/synthesize-combined）先收割 → 靠正常使用自動觸發，不需 Cloud Scheduler。
- **reap-on-cleanup**：兩服務 cleanup 端點一併收割（回傳 `reaped` 數）。
- **① content-analyser lazy 自癒**：`_sync_crawling_dataset` 加 —— dataset 卡 crawling 超過 90 分（無人輪詢、job 已死）→ 標 failed；crawler job 回 `error`（找不到）也即時標 failed，不再永遠轉圈。
- 版本：crawler 1.7.0→1.8.0、analysis 1.1.0→1.2.0。需部署三服務生效。

## 2026-06-16 部署：爬蟲佇列化上線 + 保時捷選擇器模板（content-crawler 00057-7hp / v1.7.0）
- **佇列化啟用**：merge `feat/crawl-queue` → main，部署 content-crawler concurrency=1 + `CRAWLER_USE_QUEUE=1`，
  Cloud Tasks 佇列 `crawler-tasks`@asia-east1（RUNNING、max-concurrent-dispatches=8）、compute SA 綁 enqueuer。
  crawl/extract-images/research 三條改走佇列分塊同步 worker → 杜絕多用戶並行 Chrome 疊加 OOM。秒回退：移除 `CRAWLER_USE_QUEUE`。
- **選擇器模板**：merge `feat/porsche-templates` → main（先前已部署 00055/00056）。sicar/ppaper/tvbs 新模板、gq_tw/vogue_tw 前綴選擇器。
- analysis-pipeline（00021）/ content-analyser（00032）本回合稍早已部署、即 main 程式碼，未重部。
- ⏳ 唯一未做：線上並行實爬驗證（自動模式因「停止保時捷批次」邊界擋下主動重爬；設定已靜態確認）。

## 2026-06-16 補強：爬蟲並行安全（Cloud Tasks 佇列化，已部署，feature branch）
解決「多用戶同時爬蟲 → 背景執行緒堆同台 instance → 多 Chrome 疊加 OOM」風險（背景執行緒模式騙過 Cloud Run 請求式擴縮 + CPU 節流挨餓）。改為佇列 + 同步 worker：
- **content-crawler**：新增 `task_queue.py`（Cloud Tasks 入列、`tasks_enabled()` 由 `CRAWLER_USE_QUEUE=1` 明確開關）。
  - `crawl_job.py` 重構：抽出共用 `_crawl_sequence`（看門狗/回收/Tier2-3 單一來源）；保留 `run_crawl_batch`（背景執行緒 fallback）；新增 `run_crawl_chunk`（同步單塊 worker）+ `_complete_chunk`（交易式完成計數、冪等）+ `chunk_urls`（CHUNK_SIZE=6）。
  - `image_extract.py`：同樣抽 `_extract_sequence` + `run_image_extract_chunk` + `_complete_image_chunk`（IMG_CHUNK_SIZE=8）。
  - `app.py`：`/api/crawl/batch`、`/api/extract-images`、`/api/research` 改為「佇列開時切塊入列、關時回退背景執行緒」；新增同步 worker `/api/crawl/run`、`/api/extract-images/run`、`/api/research/run`（@require_api_key；例外回 500 由 Cloud Tasks 重試，結果 doc id 以全域 index 命名→冪等覆蓋）。SERVICE_VERSION 1.6.1→1.7.0。
- **deploy.sh**：content-crawler `--concurrency 4→1`、`--max-instances 10`、注入 `GOOGLE_CLOUD_PROJECT/TASKS_QUEUE/TASKS_LOCATION`，二次 update 注入 `WORKER_URL`。
- **requirements**：`google-cloud-tasks`。
- **未啟用前零行為改變**：`CRAWLER_USE_QUEUE` 未設=0 → 全走背景執行緒 fallback（與現況相同）。需維運者手動建 Cloud Tasks 佇列 + 授權 SA + 設 `CRAWLER_USE_QUEUE=1` 才切換。
- 待：本機驗證 fallback、雲端建佇列、`核准部署：測試` tagged 驗證並行行為後切流量。

## 2026-06-16 新增：整合報告（影像服務階段③，文字 × 視覺）
把既有「文字分析報告」+「視覺分析報告」交叉整合成一份整合策略報告（兩者已同框架，整合自然）：
- **analysis-pipeline**：`combined_report.py`（新）+ `POST /api/synthesize-combined`／`GET …/<job_id>`（非同步、輕量、無爬取/圖片/NLP）。
  - **結構（文字為主體＋附加視覺）**：整合報告 = 文字報告（原文逐字保留，主體）＋ 視覺分析重點（原文保留、去逐圖附錄）＋ 整合洞察（LLM **僅生成這段**）。**不改寫兩份原報告**，確保原文字報告完全不被動到。
  - 整合洞察：內容主題×視覺模式對應、內容缺口∩視覺缺口、可操作整合建議（圖素 brief 綁內容主題）。
- **content-analyser**：`analysis_client`（submit_combined/get_combined_status）；`project_routes.combine_analyses`（選 1 文字 + 1 視覺 → 建 `kind='combined'` analyses doc → 導向報告頁）；`analysis_status` 加 combined 分流。
- **UI**：歷史分析清單加勾選框（HTML5 `form=` 屬性避免巢狀表單）+「🧩 整合所選報告」；報告頁/清單顯示「🧩 整合報告」徽章。整合報告本身列入歷史分析（第三類）。
- 只動 analysis-pipeline + content-analyser。


## 2026-06-16 改善：視覺分析重新聚焦（基準線 → 缺口 → 圖素 Brief，對齊產品方法論）
原視覺分析是「通用描述單張圖」（主觀美學形容詞、逐圖孤立、讓 LLM 猜顏色），與產品兩大方法論脫鉤、不夠準。重做為與文字分析同構：
- **受控視覺分類**（取代開放形容詞）：每張圖歸類固定維度——鏡頭類型／背景／構圖／光線／品牌符碼(多選)／可疊字留白／主體一句；分類任務 LLM 更穩、可跨圖統計。
- **主色用 Pillow 客觀色盤** + `_color_family`（hex→粗色家族）統計，不讓 LLM 猜色。
- **主題脈絡**：把資料集主題餵給 Gemini，判讀對齊主題。
- **方法論一（基準線）**：程式統計各維度分佈（含佔比）→「市場視覺基準線」。
- **方法論二（缺口）+ 圖素 Brief**：Synthesis LLM 依**客觀統計**產出差異化缺口 + 可操作圖素製作規格。
- 報告改為：一、視覺基準線（分佈表）／二、差異化缺口／三、圖素製作 Brief／附錄逐圖標註。
- 只動 analysis-pipeline（`image_report.py`）。

## 2026-06-16 新增：大圖視覺分析（影像服務階段②，收進 analysis-pipeline，已部署 analysis 00017 / crawler 1.6.1）
承接階段①的主文大圖，產出視覺分析報告（色調/色澤/主題/視覺吸睛要素），供製作圖素參考。
- **analysis-pipeline**：
  - `llm_client.generate_vision`（Gemini/Claude 原生 vision，含退避重試）。
  - `image_report.py`（新）：下載（帶 Referer 破防盜連、SSRF 防護、大小/逾時上限、AVIF→jpeg 變體重試、
    URL 與 Referer 皆 percent-encode）→ Pillow 色盤（主色 hex + 真實尺寸、二次濾小圖）→
    Gemini 視覺分析 → 彙整整體視覺趨勢 → Markdown。限併發/圖數上限（40）成本守衛。
  - `POST /api/analyse-images` + `GET /api/analyse-images/<job_id>`（非同步、不回傳 key）。requirements 加 Pillow。
- **content-analyser UI 入口**：`analysis_client` 客戶端 + `project_routes`（analyse_images_dataset/status）
  + `dataset_detail`「🎨 分析這些大圖」按鈕 + 視覺報告面板（marked+DOMPurify 渲染、可下載 MD）。
- **下載韌性修正**（CHANEL 實測 14/40 → 30/40 後逐一拆解）：
  - Stage① srcset 改**以空白切詞**（Hearst 圖 URL 內含逗號 crop 參數，原以逗號切會切爛 → 產垃圾 URL）
    + 影像 URL 合理性網（無副檔名且同主機 → 擋）。→ elle/cosmo 垃圾消失。
  - Stage② URL 與 **Referer** 皆 percent-encode（she.com 圖含中文檔名、文章 slug 含中文當 referer → latin-1 編碼錯誤）。
  - **Tier3 標註與跳過**：`TIER3_DOMAINS`（s.yimg.com 等機房IP 被封的站）遇到**直接跳過**
    （不浪費 20s 下載逾時），報告標「跳過（需 Tier3）」；下載遇 403/401 亦動態標記 skipped_tier3。
    job 加 `n_tier3`。待辦：未來補 Tier3 住宅代理下載再真正取這些圖。
  - 線上實測：補 Referer fix 後 CHANEL 隨機 40 張 → **40/40 成功**。
- 文件：附錄 B `analyse-images`、附錄 C `image_analysis_jobs`。

## 2026-06-16 新增：主文大圖擷取（影像服務階段①＋UI 入口，已部署 crawler 1.6.0 / analyser 00029）
影像視覺分析服務的第一階段：只取圖、不碰文字，擷取主文容器內的大圖（供後續色調/色澤/主題視覺分析參考）。
- **crawler 端點**（`image_extract.py` + `app.py`）：`POST /api/extract-images` + `GET /api/extract-images/<job_id>`（非同步、require_api_key、SSRF 過濾）。
  - **重用文字爬蟲主文選擇器**（learned_selectors → SITE_TEMPLATE → 啟發式）解析容器，只蒐集容器內 `<img>`/`<picture><source>`，容器外（banner/icon/縮圖）一律濾掉。
  - **靜態優先、Chrome 補位**：先抓靜態 HTML，靜態無圖才開 Chrome（JS/lazyload 站）；靜態能取圖的站完全不啟動 Chrome。
  - **輕量過濾**：srcset 取最大、lazy 屬性、絕對化、去重、廣告網域、icon/logo/sprite 路徑、`.svg`、明確小尺寸（<200px）。
  - **隨機抽樣**：蒐集全部合格大圖（安全上限 300）後 `random.sample` 抽**最多 10 張**（樣本不偏向版面開頭），依版面順序輸出。
  - 結果寫 `image_extract_jobs/{id}/results`。與文字爬取 `scrape()` 嚴格分離（不並行、不互相拖累）。
- **content-analyser UI 入口**（補「用戶操作邏輯漏洞」：先前只有 crawler 端點、無入口）：
  - `crawler_client`：`submit_extract_images` / `get_extract_images_status`。
  - `project_routes`：`extract_images_dataset`(POST，Editor↑，取成功項 URL) + `extract_images_status`(GET 輪詢)，job_id 存 `dataset.image_job_id`。
  - `dataset_detail.html`：completed 時「🖼 擷取主文大圖（N）」按鈕 + 結果面板（每篇隨機抽到的大圖縮圖牆，連原圖）。
- **端到端實測通過**（CHANEL 聖誕資料集，經正式 UI）：19 個成功項 → **131 張大圖**；voguehk 10 張全為 CHANEL 聖誕珠寶/腕錶主文大圖、零 banner/icon；SSR 站走靜態、elle/mintnews 等走 Chrome 補位。
  - 註：Hearst（elle）CDN 防盜連使縮圖在站內內嵌破圖，但 URL 正確、原圖可開；階段②下載時帶 referer 即可解。
- 文件：附錄 B 新端點、附錄 C `image_extract_jobs`。

## 2026-06-16 新增：選擇器研究工具（on-demand AI agent，B1+B2，已部署 crawler 00052 / analyser 00028）
爬完出現失敗項時，用戶可觸發「選擇器研究」——一個 in-code tool-use 閉環 agent，研究失敗網域、產出候選選擇器或失敗診斷，經 admin 確認後升級為主爬蟲知識：
- **B1（crawler 核心）**：`research.py` agent 閉環（開樣本→Gemini 提選擇器→**實測抽到幾字/像不像正文**→不夠好回饋再修，上限 6 步/120s/域→第二樣本交叉驗證）。重用 `HeadlessCrawler` 的 Chrome/DOM 工具（不肥大主爬蟲）；用系統 GENAI_API_KEY；與爬蟲不並行。失敗則分類診斷（403→Tier3 / JS空殼 / 列表頁 / 無正文）。`POST /api/research` + `GET /api/research/<job_id>`（非同步）。`site_learning` 加候選 CRUD + promote。
- **B2（content-analyser）**：資料集「🔬 研究失敗項」按鈕 + 結果輪詢面板；`/admin/selector-candidates` 候選確認頁（升級→寫 learned_selectors / 拒絕）。
- **安全/隔離**：候選 per-domain（錯誤鎖死單一網域）+ admin 人工確認才升級 + 主爬蟲讀取端驗證三重把關。
- **B2+ admin 主動研究**：`/admin/research-url` 讓 admin 貼任意 URL 主動研究（不限失敗項），供測試/主動建模板。
- **agent 調校（測試後）**：放寬 is_listing 硬拒（正文含「延伸閱讀」連結不再誤判列表）；prompt 引導選**可泛化**選擇器（避開含文章編號的 id）。
- 文件：附錄 B 新端點、附錄 C `research_jobs`/`selector_candidates`/`learned_selectors`。
- **端到端實測通過**：(1) skm 403 → 正確診斷「建議 Tier3」；(2) marieclaire 2 樣本 → 產出可泛化候選
  `…article > div.articleContent`（交叉驗證 2300 字）→ admin 確認升級 → 寫入 learned_selectors（測試後已移除該筆，保護其既有模板）。
- 部署：crawler + content-analyser（皆 image-only）。

## 2026-06-16 改善：爬蟲最佳化（Phase 1+2，已部署 content-crawler 00050-k89）
徹底研究爬蟲流程後，完成低/中風險最佳化（中高項 Fetch.enable/research 模式記入待辦監督式開發）：
- **Phase 1（穩定/偵測）**：
  - 已學/快取選擇器**讀取端驗證**（字數≥300+非列表+非cookie 才採用）→ 修補誤學寬選擇器污染整域。
  - `AD_BLOCKLIST` **外部化**（`get_ad_blocklist`：內建 + Firestore `system/config.ad_blocklist`，60s 快取）→ 不重部署即可增刪。
  - 深滾傳入 **deadline**（剩餘<30s 停滾保留抽取時間）。
  - crawl_job 看門狗/回收改 `_force_close`（close 超時保護 + 先關舊再建新）→ **消除新舊 Chrome 並存 OOM 窗口**。
  - 每篇結果加觀測 metric（`elapsed_sec`/`hung`）。修正誤導 log。
- **Phase 2a（速度/未知站偵測）**：**SSR 輕量預探測**——未知站開 Chrome 前先抓靜態 HTML，JSON-LD/RSC 結構化內文≥1000字即跳過 Chrome（省 16–40s 冷啟動）。僅對未知站套用，已知模板站零影響。env `CRAWLER_SSR_PROBE=0` 可停。
- **效能比較（CHANEL 20 網址，同資料集）**：

  | 版本 | 成功率 | HUNG | 重點 |
  |------|--------|------|------|
  | 基準（最佳化前） | 19/20 | — | skm 403 為唯一失敗 |
  | Phase 1 | 19/20 | 0 | 無退步、無 hang；sum 1639s |
  | Phase 1+2 | 19/20 | 0 | sum 1628s；**tvbs（未知SSR站）68s→3.7s（18×）且 1954→3708字** |

  19 個已知模板站不受 SSR 預探測影響（正確跳過）；唯一未知 SSR 站 tvbs 大幅提速且抽取更完整。
- 回捲檢查點：`rollback-20260616-crawler-opt`（最終測試通過，未使用）。
- 待辦（中高，監督式）：Fetch.enable 依型別封鎖、research「先研究再爬」模式（chip task_077594ed）；redirect-SSRF/同步看門狗（task_186abfdc）。

## 2026-06-15 修正：全面 code review + 安全審查問題修正（已部署）
完整審查（5 路並行深審 + 驗證）後，修正所有 Critical/High 與多數 Medium 問題。分 5 批提交：
- **安全批 1**：SSRF（crawler `_is_safe_url` 對 domain 解析 DNS、任一 IP 落內網即拒，補「域名指向 metadata」繞過）；
  SECRET_KEY 正式環境 fail-fast；安全標頭（CSP/X-Frame-Options/nosniff/HSTS）；/debug 非 dev 回 404；前端 bootstrap/marked/dompurify 鎖版+SRI。
- **安全+韌性批 2（analysis）**：prompt_safety（INJECTION_GUARD + `<DATA>` 包裹）防爬取內容注入 LLM；
  llm_client 對 429/5xx 指數退避重試、Claude 回應防呆、Gemini fallback 收窄；Path1 逾時 race 修正（丟棄部分結果+凍結 search_extent 快照）；失敗批次顯性化；report §3 連結 scheme 白名單。
- **邏輯批 3（content-analyser）**：爬蟲續批副作用 gate 給 editor+（Viewer 唯讀輪詢不再 spawn）+ 交易式認領防雙開重複續批；
  analyse_dataset 補 100 篇上限+50k 截斷；_save_dataset_items _seq 交易式預約防併發碰撞；list_models 只准查相符 provider 防金鑰外洩；OAuth email 正規化小寫。
- **穩健批 4**：crawler hard_timeout 夾值、學習選擇器驗證、crawler/analysis cleanup 改 where 過濾+limit；nlp_path embeddings 對位降級；search_extent_client 非 2xx 一律 error。
- **納入開發程序**：較大項（crawler redirect SSRF 殘留 / 同步看門狗 / close 超時、list_projects 全集合掃描）拆背景任務並記於 OPTIMIZATION.md；search-extent §7 e2e 阻塞於 Ads token。
- 全部 py_compile 通過；已部署（四服務 image-only；content-analyser SECRET_KEY 由 Secret Manager 注入）。

## 2026-06-15 文件：根目錄三支柱整理（產品/開發/維護中樞）
把雜亂的 11 個根目錄 .md 整理成「三支柱 + 索引中樞」結構，零資料刪除、保留完整開發紀錄：
- **新增 [DEVELOPMENT.md](DEVELOPMENT.md)（開發中樞）**：索引 development_plan / CODE_REVIEW / OPTIMIZATION / SECURITY_INCIDENTS /
  FRONTEND_HANDOFF / changelog，記錄各文件用途與「何時更新」+ 現況里程碑。
- **新增 [MAINTENANCE.md](MAINTENANCE.md)（維護中樞）**：技術棧（四服務規格）、維護腳本一覽（deploy/rotate-key/setup_admin/devserver/setup_secret）、
  金鑰與 Secret 全貌、日常 vs 完整部署、從零初始化（索引 DEPLOY_CHECKLIST）、崩潰復原/回滾、健康檢查。
- README 文件導覽改為三支柱 + 四服務；CLAUDE.md 加文件地圖。
- 被索引文件加「歸屬 + 現況」標註（保留歷史本文）：development_plan（Phase 0–4 完成）、CODE_REVIEW（部分已處理）、
  OPTIMIZATION（補 2026-06-15 技術債盤點）、DEPLOY_CHECKLIST（標註三→四服務差異與新 secret）、SECURITY_INCIDENTS、FRONTEND_HANDOFF。
- 規則：三支柱為索引中樞、不重複內容；開發時依「改什麼→更新哪份」對應。

## 2026-06-15 補強：鎖定服務間驗證金鑰 + 新增 rotate-key.sh 安全輪換腳本（已部署 content-analyser 00024-84b）
- **後台移除可編輯入口**：`CRAWLER_API_KEY` / `ANALYSIS_API_KEY` 是服務間共用 X-API-Key（驗證方+呼叫方各一份、值需一致），
  從 `ALLOWED_SECRETS` 與後台下拉移除（後端 update_secrets 自動拒絕 + UI 不顯示），避免隨手改一端造成中斷。
- **新增 `rotate-key.sh <CRAWLER|ANALYSIS>`**（根目錄）：以維運者 gcloud 身分原子化輪換——產生新值 → 寫 Secret Manager →
  重部署驗證方+呼叫方 → 用新金鑰打受保護端點驗證（看狀態碼非 401/403）。金鑰不 echo/不落地/結束 unset；
  不擴充 service account 權限（安全界線不變）；驗證失敗印回滾指引。單金鑰輪換有 1–2 分鐘空窗（離峰執行）。
- CLAUDE.md §3.1 補金鑰輪換程序說明。

## 2026-06-15 新增：後台管理 Tier 3 代理憑證 + deploy.sh 標準化（已部署 content-analyser 00023-jgq）
把 content-crawler 住宅代理憑證從「Cloud Run console 明文 env」標準化為 Secret Manager，並可由管理後台維護：
- **後台管理憑證**：`/admin` 的「Secret Manager 金鑰管理」下拉新增「Tier 3 代理憑證」(PROXY_HOST/PORT/USER/PASS/PROVIDER)，
  比照 GENAI key（只輸入不回顯、admin_required、CSRF）。`services.set_secret` 改為「secret 不存在則自動建立」(create_secret + add_version)，首次設定可純後台完成（需 SA 有 secretmanager.secrets.create）。
- **deploy.sh 標準化**：crawler 段以 `--set-secrets` 注入 5 個 PROXY_* + CRAWLER/GENAI key，`ENABLE_YOUTUBE_TRANSCRIPT` 走 `--set-env-vars`；
  移除原本會清空 console env 的 `--clear-env-vars`。Tier 3 on/off 由後台 toggle（Firestore tier3_enabled，fail-closed）控制，不再用 PROXY_ENABLED env。
- **安全**：憑證只存 Secret Manager（不進 Firestore、不進 git/image/Cloud Build、不放 console 明文）；更新後需重啟 crawler 才生效。
- 部署：content-analyser image-only（00023-jgq）。content-crawler 已重部署（00047-whj）：5 個 PROXY_* 改從 Secret Manager 讀取（valueFrom secretKeyRef），console 明文 PROXY_* 已移除；PROXY_ENABLED 一併移除，Tier 3 on/off 改由後台 toggle。crawler SA 具專案層 secretAccessor，部署無權限問題。
- 文件：CLAUDE.md 三服務→四服務（含 search-extent）、附錄 C 補子集合、§6.2/附錄 D 補 PROXY_* 與語法檢查清單。

## 2026-06-15 修正：/admin/users 白名單管理 500（已部署 content-analyser 00022-mlc）
症狀：管理員開白名單頁整頁 500，無法審核（恰好有真實待審用戶卡在 pending）。
- **根因**：`users/{email}` 以 email 為 doc ID，但 `list_all_users` 用 `to_dict()` 丟失 doc ID；
  早期建立的 admin 文件又缺 `email` 欄位 → template `url_for(..., email=None)` → Werkzeug BuildError → 500。
- **修正**：
  - `services.list_all_users`/`list_pending_users`：以 doc ID 注入 email（`_user_dict_with_email`），對缺 email 欄位的舊文件永久免疫。
  - `services.ensure_user`：admin 登入時 upsert 完整 user 文件（email/display_name/picture/whitelist_status=approved/is_admin），自動修復畸形文件；空值不覆蓋既有。
  - `admin_users.html`：admin 該列顯示「管理員」徽章、隱藏審核按鈕（防自我停用）。
- 驗證機制（Google OAuth → ensure_user → 非 approved 擋至 /pending）原本即完整，本次僅修管理頁與 admin 記錄。實測頁面恢復、按鈕正常。

## 2026-06-15 補強：分析 LLM 並行化 + 爬蟲降冷啟動（已部署 analysis 00014-bqd / crawler 00046-cfh）
針對執行速度做精簡優化，不犧牲穩定與功能完整（prompt/輸出/取消檢查點全不變，併發皆設上限防 rate limit）：
- **analysis-pipeline Phase 1（加速）**：
  - `synthesis.py`：§1/§4/§6/§7 四章節由序列改 `ThreadPoolExecutor(4)` 並行；新增 `_safe_gen` 包裝，單章失敗沿用原 fallback、不影響其他章節。
  - `llm_path.py`：意圖萃取各批次並行（上限 4，`ex.map` 保序）；`run()` 內 Path 2a 意圖 ‖ Path 2b 質化同時執行。
  - `pipeline.py`：`label_clusters` 移入 Path 1 thread（與 Path 2 並行），且先於 search-extent → 真實關聯關鍵字能用上正式群標籤。
- **content-crawler Phase 2**：`crawl_job.py` `RECYCLE_EVERY` 6→12（廣告/追蹤封鎖+關圖後記憶體大降，driver 冷啟動 16–40s 次數減半）。
- 部署：兩服務皆 image-only（`:perf-20260615`），保留 crawler console 設定的 `PROXY_*` / `ENABLE_YOUTUBE_TRANSCRIPT` 等 env。Phase 3（平行爬取）未動。

## 2026-06-15 新增：自動續批 + esquirehk listing 修正 + 結果（13/20）+ overlay-skip/時限驗證
第二次 CHANEL 測試 13 成功/1 略/6 失（前兩次 5、0）：HK 站(voguehk/popbee/elle.hk)+重 TW 站全回，403 正確判失敗。
- **自動續批（auto-continue）**：批次撞 45 分上限/連續卡死切掉的項標 `unattempted=True`；`_sync` 完成時若有未爬項
  → 自動再開一批補爬（保留已成功項，merge），最多 15 輪。真失敗(403/卡死)不自動重試。→ 解決「批次被切掉沒爬完」。
- **esquirehk listing 誤判**：模板/已知容器命中時跳過列表頁檢查（文章頁多個 <article> 卡片被誤判 skip）。
- 已知殘留：harpersbazaar/tw gallery 頁（g 開頭，數百圖）載入卡死（有模板+overlay-skip，仍卡在 load；看門狗擋住）。

## 2026-06-14 補強：縮短爬蟲時限 + 403偵測 + 4個HK站模板（已部署 00043-8fq）

## 2026-06-14 重構：批次無數量上限（items 子集合）+ 重啟續爬（待部署 crawler+analyser）
解除「批次數量上限」與「永遠卡住/被切掉就得重來」：

- **③a crawler results 子集合**：`crawl_jobs/{id}/results/{idx}`（不再內嵌於 job 文件）→ 無 1MB 上限。
  job 文件保持輕量；get_crawl_job 依 `__name__` 組裝；cleanup 連同子集合刪除。crawl_batch 上限 100→1000。
- **③b dataset items 子集合**：`projects/{pid}/datasets/{did}/items/{auto-id}`（`_seq` 排序）→ 無 1MB 上限、筆數不限。
  新 helper `_load/_save/_delete_dataset_items`、`_replace_items_by_url`。所有讀 items 點（詳情/一鍵分析/合併/下載/刪除）改讀子集合。
  create_dataset/manual 上限 100→1000，移除舊 90 萬字守衛。**分析端不受影響（contents 仍經 HTTP 傳入，與儲存解耦）。**
- **④ 重啟續爬** `POST /<pid>/datasets/<did>/recrawl`：mode=failed（只重爬未成功、保留已成功項並合併，靠 `recrawl_urls` + _replace_items_by_url）
  / mode=all（整份重爬）。dataset_detail 加「🔄 重爬未完成/失敗項」「↻ 重爬全部」鈕。→ 批次被時限切掉的部分按一下續爬。
- 註：分析仍「單次最多 100 篇」（既有保護，與爬取無上限分離）。

## 2026-06-14 補強：縮短爬蟲時限 + 403偵測 + 4個HK站模板（待部署 crawler）
針對 HK 站 batch 卡死後續優化（第一段，crawler-only）：
- **縮短單頁時限**：內部 300→120s、看門狗 360→160s（正常頁 <90s 不受影響；hang 早砍，省下時間讓更多頁爬得到）。
  批次總時限 1800→2700s（搭配重啟續爬）。
- **403/HTTP 錯誤頁偵測**：`_looks_like_http_error_page`，內容極短且符合 403/404/forbidden 等特徵 → 判 failed
  （修 skm.com.tw 回「403 Forbidden」28 字卻被當 success）。
- **4 個 HK 站模板**（Chrome MCP 實測驗證）：voguehk `.article__body-main`、popbee `article.post-body-article`、
  ellehk/esquirehk（Hearst HK eZ/Ibexa）`article`。皆 SSR 無遮罩；卡死主因是廣告 script 拖住 page-load，
  模板命中→容器已知→淺滾，配合縮短時限緩解。

## 2026-06-14 修正（重大）：爬蟲防卡死機制 — 單頁看門狗 + 連續卡死中止 + 批次總時限
嚴重 bug：單頁在某步驟「內部」hang（如 voguehk 通用後備遮罩處理），checkpoint 式 300s 硬時限攔不住
→ 整批永久凍住、持續耗時與費用。完整三層防護（crawl_job.py）：

1. **單頁看門狗**：每篇在獨立 thread 跑（`_scrape_with_watchdog`），超過 `PAGE_WATCHDOG=360s`（內部硬時限 300+緩衝）
   仍未回 → 判卡死、**砍掉並重建 driver**（解除卡住的 Chrome、abandon hang 中的 thread）、標記 failed、續下一篇。
2. **連續卡死中止**：連續 `MAX_CONSECUTIVE_HANGS=3` 篇看門狗逾時 → 疑系統性問題，提前中止整批、剩餘標未爬。
3. **批次總時限**：整批超過 `BATCH_MAX_SECONDS=1800`（30 分）即收尾，剩餘標未爬。
- 註：同步 /api/scrape 由 Cloud Run request timeout(300s) 界定；非同步批次（背景 thread 無 request timeout）才是漏洞，故守衛置於 crawl_job。

## 2026-06-14 新增：網址清單容錯解析（修正貼入被編碼黏成一坨，待部署）
資料集網址清單貼入時，若換行被編碼（%0A%0A）會被當成單一超長網址（job 1/1）。
- `parse_url_list()`：還原 %0A/%0D/%20、用空白/換行切、lookahead 拆開黏在一起的連續 http(s)://、去重保序、只留 http(s)。
- `create_dataset` 改用此解析（取代 splitlines）。涵蓋真換行/編碼換行/空白分隔/完全黏住。

## 2026-06-14 修正：爬蟲 OOM — 模板判別優先 + 批次回收 driver（待部署 crawler）
香港/時尚 listicle 批次爬到一半 Worker SIGKILL（OOM）→ 整批中斷。診斷：批次重用單一 driver，
Chrome 記憶體隨頁數累積；HK 站（hk.news.yahoo/popbee 無模板）深滾整頁加速膨脹。

- **治本｜模板判別優先（crawler.py scrape）**：把「內容容器判別」移到滾動前。
  ① 初始 DOM 足量 → 淺滾；② 容器已知（模板/已學選擇器）→ 淺滾；
  ③ 未知站 → 先用 Gemini 對初始 DOM 即時學選擇器（存 site_learning），學到 → 淺滾，否則才深滾。
  新方法 `_content_container_known()`、`_discover_selector_on_initial_dom()`。深滾（記憶體元兇）只在真正找不到容器時才跑。
- **記憶體保險｜crawl_job 每 6 篇回收重建 driver**（RECYCLE_EVERY=6），釋放 Chrome 累積記憶體；
  學到的選擇器已持久化 Firestore，回收不遺失。
- **併發 4→2**（部署參數），降低同實例多 Chrome 疊加 OOM 風險。

## 2026-06-14 LLM 設定介面改版：三家提供商 + 模型下拉 + 進階參數（待部署）
分析模型選擇介面強化（簡易/進階雙模式）。

- **三家提供商**：Gemini / Claude / **ChatGPT(OpenAI)**。`llm_client.py` 加 OpenAI 分支
  （chat.completions，o 系列自動改用 max_completion_tokens 重試）；requirements 加 `openai`。
- **模型下拉**：內建精選清單（依提供商切換）+「其他（自填）」逃生口 + 🔄「取得最新模型」按鈕
  （新端點 `GET /<pid>/models?provider=`，用專案 key REST 抓各家 list-models）。
- **進階參數（A+B）**：簡易模式只露 提供商/模型/Key/溫度；進階模式露：
  - (A) **輸出長度上限** max_output_tokens（256–32768，預設 8192；LLMClient 可設、synthesis 各段用此上限）。
  - (B) **輸入內容量** input_scale（標準/加大/最大；放寬 llm_path 每篇字數與篇數，利用大 context window）。
  - top_p、Gemini thinking、search-extent 開關（搬進進階）。
- 串接：project 設定 → llm_config → submit_analysis → /api/analyse → pipeline → LLMClient/llm_path。
- `llm_config` 新增欄位：max_output_tokens / top_p / input_scale。

## 2026-06-14 專案操作：編輯/更名 + 封存 + 刪除 + 強制刪除（待部署）
content-analyser 專案層級操作補強（Owner/Admin）。

- **編輯/更名**：`POST /<pid>/edit` 改 title + description。
- **封存/還原**：`POST /<pid>/archive`（`archived` 旗標）。封存後 **Editor/Viewer 無法進入**
  （`project_access_required` 加 gate），Owner/Admin 仍可進入；列表中**灰階呈現**+「已封存」標記+還原鈕。
- **刪除**：`POST /<pid>/delete`（Owner/Admin）。**先檢查無執行中相依工作**（dataset crawling / analysis pending|running）；
  有則擋下提示，無則級聯刪除（datasets + analyses + 專案）。
- **強制刪除**：`POST /<pid>/force-delete`（**僅系統 Admin**）。處理卡住的專案：先取消所有執行中工作再整個刪除。
- Firestore：`projects/{pid}` 加 `archived`(bool) / `archived_at`。usage_log 記 edit/archive/unarchive/delete/force_delete_project。
- UI：project_detail 新增「專案管理」卡（編輯/封存/刪除/強制刪除）；projects 列表封存灰階+還原。

## 2026-06-14 B2：search-extent 接入分析報告 §7（真實搜尋接地，待部署）
把 search-extent 串進 analysis-pipeline，報告 §7 延伸關鍵字改用 Google 真實搜尋量佐證。

**analysis-pipeline**：
- 新 `search_extent_client.py`（HTTP 呼叫 search-extent /api/expand；URL/Key 未設或失敗則靜默略過，不影響報告）。
- `pipeline.py`：Path 1 分群後，用各群 top 關鍵字（最多 6 群×5 詞）呼叫 search-extent，**與 Path 2 並行**；
  結果 `search_extent_results` 傳入 synthesis 與 report。開關 `search_extent`（預設開）+ 須 URL/Key 已設定。
- `synthesis.py` §7：有真實資料時改走「真實資料接地」prompt（標搜尋量、判斷未涵蓋缺口）；否則退回純語意推論版。
- `report.py`：§7 標題依是否接地調整；新增「附錄：真實搜尋延伸資料」表（依群列關聯詞+月均搜尋量+競爭度）。
- `app.py`：接收 `search_extent`（預設 True）→ llm_config。requirements 加 `requests`。

**content-analyser**：
- `analysis_client.submit_analysis` 加 `search_extent` 參數；3 個提交點傳 `llm_config.search_extent`。
- `update_settings` 讀 checkbox；專案設定 UI 新增「啟用搜尋延伸接地」開關（預設開）。

**部署相依**：analysis-pipeline 需注入 `SEARCH_EXTENT_SERVICE_URL` + `SEARCH_EXTENT_API_KEY`（先部署 search-extent 取 URL）。
未注入時 pipeline 自動略過，報告照常產出（純 LLM §7）。

## 2026-06-14 新增第 4 微服務 search-extent（B1：Ads Keyword Planner 關鍵字延伸，服務已部署 deploy-20260614-3；§7 實際資料仍阻塞於 ADS_DEVELOPER_TOKEN）
延伸服務 B 第一階段。獨立 Cloud Run 服務已部署（deploy-20260614-3）；§7 實際資料等 Google Ads API Basic access 核准補 dev token 才會回真實值。

**search-extent（需求側情報）**：
- 種子關鍵字（分析語意群 TF-IDF top 詞）→ `KeywordPlanIdeaService.GenerateKeywordIdeas`
  取關聯關鍵字 + 平均搜尋量 + 競爭度。唯讀，不投放/不變更帳戶。
- API：`GET /health`、`POST /api/expand`（X-API-Key，需 'expand' 權限）。預設語言 1018（繁中）、地區 2158（台灣）。
- 檔案：`search-extent/`（app.py / ads_client.py / auth.py / Dockerfile / requirements.txt / README.md / gen_refresh_token.py）。

**GCP 設定（已完成）**：
- 啟用 Google Ads API（content-analyser-cn）。
- 建 OAuth client「search-extent Ads API」(Desktop)；client_id/secret 存入 Secret Manager。
- 已建 secrets：`ADS_CLIENT_ID`、`ADS_CLIENT_SECRET`、`ADS_LOGIN_CUSTOMER_ID`（1762473192）。
- 待補：`ADS_REFRESH_TOKEN`（跑 gen_refresh_token.py）、`ADS_DEVELOPER_TOKEN`（等 Basic access 核准）、`SEARCH_EXTENT_API_KEY`。

**架構**：Cloud Run 服務由三個增為**四個**（+search-extent）。

## 2026-06-14 資料治理四features：刪除/更名 + 孤兒清理 + 強制停止 + usage_log + LLM 精緻調配（已部署 deploy-20260614-2）
四項一次開發，已部署（deploy-20260614-2）。涉及三服務（新增取消/清理 API → 重新部署 crawler 與 analysis）。

**#9 LLM 精緻調配（analysis-pipeline + content-analyser）**：
- 專案設定新增「溫度（0–1 slider）」與「Gemini 2.5 思考模式開關」。
- LLMClient 接受 temperature/thinking；Gemini 2.5 預設 thinking_budget=0（避免思考吃掉輸出截斷），
  用戶可開啟。串接：專案設定 → llm_config → submit_analysis → /api/analyse → run_analysis → LLMClient。

**#7 刪除 / 更名資料集與報告 + 孤兒清理（content-analyser）**：
- 資料集：`POST /<pid>/datasets/<did>/rename`、`/delete`。
- 報告：`POST /<pid>/analyses/<aid>/rename`、`/delete`。
- Admin 孤兒清理 `POST /admin/cleanup`：呼叫 crawler/analysis 的 cleanup 端點，
  刪除已結束（completed/failed/cancelled）且超過 N 天（預設 7）的 job 暫存文件。
- 詳情頁與列表皆加更名／刪除 UI（Owner/Editor）。

**#8 強制停止爬取與分析（三服務，合作式取消）**：
- 新增取消端點：crawler `POST /api/crawl/<id>/cancel`、analysis `POST /api/analyse/<id>/cancel`，
  設 Firestore `cancel_requested=True`。
- 背景任務於檢查點檢查旗標：crawl_job 每篇前；pipeline 啟動後/Synthesis 前/組裝前。
  收到即轉 `cancelled` 並停止（含昂貴的 Synthesis LLM）。
- content-analyser 的 delete 路由：若仍在執行則先呼叫 cancel（廢除執行階段），再刪除記錄與資料。

**#10 usage_log（content-analyser）**：
- `users/{email}/usage_log/{id}`：{action, detail, count, project_id, at}。
- 記錄 crawl / manual_import / analyse / stop / delete 事件。
- Admin `GET /admin/usage`：各用戶用量彙整 + 最近 100 筆事件。

**新增服務 API**：crawler `POST /api/crawl/<id>/cancel`、`POST /api/crawl/cleanup`（v1.5.0）；
analysis `POST /api/analyse/<id>/cancel`、`POST /api/analyse/cleanup`（v1.1.0）。皆需 X-API-Key。

## 2026-06-14 分析品質強化 + Cowork 整合 + YouTube + Decodo + Tier3 開關（三服務皆部署）
線上版本：content-crawler 00036 / content-analyser 00013 / analysis-pipeline 00009。

**分析品質（analysis-pipeline）**：
- **A 延伸關鍵字與內容缺口（新 §7）**：synthesis 新增第 4 次 LLM 呼叫，跳出 dataset 推論
  「受眾相關但本批未涵蓋」的延伸關鍵字 / 內容缺口（差異化切點）/ 周邊主題。對應產品方法論二。
- **斷詞三層強化**：jieba 改用繁體 `dict.txt.big`（58 萬詞+詞頻，Dockerfile build 時下載、gitignore）
  為基底 + 美妝領域詞典（~70 成分/品牌）+ moedict 蒸餾補充（g0v/moedict-data 教育部辭典萃取，
  精簡為 2-3 字現代詞 10.8 萬，去成語層）。修正「維他命→他命」類切碎。
- **TF-IDF `ngram_range=(1,2)`**：單詞+雙詞（初生光采、美白精華），對齊 REF 範本。
- **分群 LLM 描述**：每群加「代表詞彙（群內 TF-IDF Top8）+ LLM 標籤 + 一句話定位」（report §3）。

**Cowork 整合（content-analyser）**：
- 手動/上傳建資料集（create_manual_dataset）：貼 JSON/上傳檔 → status=completed 資料集，可直接分析。
- 多資料集合併分析（analyse_combined）：勾選多個 dataset → 合併 contents 一次分析。

**爬蟲（content-crawler）**：
- **YouTube 影片資料化**：Tier1 oEmbed/og 取標題+說明；Tier2 Gemini 2.5 影片理解取口白逐字稿
  （env ENABLE_YOUTUBE_TRANSCRIPT）。實測 source=youtube+transcript。
- **Tier 3 代理 provider-agnostic + 後台開關**：load_proxy_config 讀 Firestore `system/config.tier3_enabled`
  （admin toggle，60s 快取）覆寫 env。Webshare→Decodo（residential）。
- 結論：**Decodo 住宅 IP + 無頭 Chrome 仍過不了 Dcard Cloudflare**（指紋偵測，非 IP），
  正解為 Decodo Site Unblocker/Scraping API 或 Chrome MCP；Tier 3 已關閉。

**CHANEL 資料集重爬**：平行 sync 22 篇（19 成功 5.9 萬字），寫回 Firestore 供分析。

**待辦 B**：另開爬蟲抓 Google 相關搜尋/autocomplete/PAA + Ahrefs/Google Trends，用真實搜尋量驗證 A 的延伸推論。

## 2026-06-14 adaymag 廣編全頁修正 + Dcard 機制研究（已部署 00032-hgd）
- **Fix（A Day Magazine 廣編全頁 .fullpage-content）**：Chrome MCP 研究發現 adaymag CHANEL 文章用
  fullPage.js，內文容器 `.fullpage-content`（非標準 `.entry-content`），SSR HTML 即有 1567 字，
  但 JS 渲染後被清空（headless body 剩 75 字）。修正：adaymag 模板加 `.fullpage-content`；
  fallback 鏈新增「DOMContentLoaded 初始快照抽取」（內容過短時從 dom_snapshot_source 重抽 SSR 原文）。
  線上實測 3/3 穩定 success、1542 字、無 crash。
- **研究結論（Dcard 反制）**：Chrome MCP 確認 Dcard 用 **Cloudflare WAF 封鎖 datacenter/server IP**
  （403 "Attention Required"，非 JS 挑戰）。所有 UA（瀏覽器/facebookexternalhit/Googlebot/Twitterbot）
  從 datacenter IP 都 403；使用者住宅 IP 的真實瀏覽器可正常載入（含 og:description 文案）。
  → og/社群 UA 手法**無效**；唯二反制 = (a) residential proxy（付費），(b) Chrome MCP 真實瀏覽器蒐集。

## 2026-06-14 CHANEL 社群來源處理：Threads/Instagram og 文案 + Cloudflare 挑戰頁偵測（已部署 00031-vhk）
- **Threads/Instagram 公開貼文（新增 `_fetch_og_meta()`）**：用 `facebookexternalhit` 社群 UA 抓
  og:title/og:description（連結預覽機制，不啟動 Chrome）。Threads 直接取得文案；Instagram 從
  Cloud Run IP 也成功取得 og 文案（本地 curl 被擋但 Cloud Run IP 可）。實測：Threads 57 字、IG 632 字文案。
  （IG 若某些 IP 被封則回 skipped 並提示 oEmbed/手動。）
- **Dcard 改走 Tier 1→3 並偵測 Cloudflare 挑戰頁**：移除 Dcard 硬跳過。Dcard 用 Cloudflare，
  headless 抓到「需要確認您的連線是安全的／Enable JavaScript and cookies」挑戰頁（302字），
  原本當假成功。`_BROWSER_ERROR_MARKERS` 加入 Cloudflare 挑戰頁特徵 → 判失敗 → 觸發 Tier 3 代理。
  實測確認：Tier 1（直連）→ Cloudflare 擋；Tier 3（Webshare datacenter proxy）→ 仍 Cloudflare 擋 → failed。
  **證實 Dcard 擋所有無頭瀏覽器，免費 datacenter proxy 無法突破，需 residential。**
- CHANEL 資料集（22 網址）最終：時尚媒體 18 篇正常抽取、Threads/IG 5 篇取得文案、Dcard 3 篇
  正確失敗（需 residential）、adaymag 1 篇 Chrome crash（待查）。

## 2026-06-14 CHANEL 資料集實測修正：Hearst 國際網域 + Vogue 列表頁誤判（已部署 00029-zsh）
- 重爬 test 專案 CHANEL 資料集（22 網址）找實際錯誤，修正並線上驗證：
- **Fix（Hearst 國際網域 indicator）**：CHANEL 用 `www.elle.com/tw`、`cosmopolitan.com/tw`、
  `harpersbazaar.com/tw`（Hearst 國際站 HTTPS）≠ 模板 indicator `*.com.tw`（台灣站）→ 不命中、落啟發式過短。
  三個 Hearst 模板補國際網域 indicator。線上實測：elle 542→**5068**、cosmo 1742→**6288**、bazaar 714→**8369**。
- **Fix（Vogue 文章誤判列表頁被跳過）**：Vogue/GQ 單篇文章 JS 渲染後載入多張關聯 `<article>` 卡片，
  觸發「≥5 article = 列表頁」誤判 → 整篇 skip。scrape() 列表判定後若有 JSON-LD articleBody（≥200 字）
  或 `og:type=article` 則否決，視為單篇。線上實測 3 篇 vogue：oaoabeauty 1868、2026-apr 2489、
  content-38996 579（原本 skip/fail → 全 success）。
- **已知未修（待決策）**：Instagram ×4 回假 success（登入牆 ~400 字「Instagram」頁，非貼文）；
  threads 57 字（登入牆）；adaymag Chrome crash「session not created」（重頁面不穩定，crawler 會 recover 報 failed）；
  dcard ×3 正常 skip（需登入）。
- 部署 00027→00029（皆唯一 tag）。Tier 3（Webshare）維持啟用。

## 2026-06-14 Webshare Tier 3 實測 + Hearst CMS 模板修正 + 瀏覽器錯誤頁偵測（已部署 00027-8vs）
- **Tier 3 實測通過**：用 Webshare 免費 Rotating Proxy Endpoint（`p.webshare.io:80`，10 datacenter IP）實測：
  curl 驗證 IP 輪換（38.x→84.x）；爬蟲端 log 確認 `[Tier3] Webshare proxy（含驗證）已掛載`、
  Tier 1 內容過短時自動觸發代理重抓、proxy auth 擴充在 headless Chrome 成功運作。
  憑證存 gitignored `.env`、Cloud Run env var（測試用，非 Secret Manager）。
  ⚠️ 免費 datacenter proxy 無法繞過強反爬蟲（需 residential 付費）。
- **Fix（ELLE/Cosmo/Bazaar 為 HTTP-only）**：DNS 指向 Fastly `nonssl` 端點，**https 連線一律失敗**，必須用 `http://`。
- **Fix（Hearst 新版 CMS 選擇器）**：Hearst 改版，主文容器從 `.article__body-content` 改為
  `.listicle-body-content` / `.content-container` / `[class*=body-content]`。elle_tw/cosmopolitan_tw/
  harpersbazaar_tw 前置新選擇器。實測 ELLE 清單文從 580 字（推薦區）→ **2325 字正文**。
- **Fix（瀏覽器錯誤頁誤判為成功）**：站台連不上時 Chrome 渲染 neterror 頁，原本被當正文回 `success`。
  新增 `_looks_like_browser_error_page()`（偵測 "site can't be reached"/"refused to connect"/ERR_*）；
  套用至正常路徑、逾時部分內容路徑；並在 `_open` 後早期偵測 `<body class="neterror">` 快速判失敗
  （避免白等逾時、讓 Tier 3 提早接手）。實測 https ELLE 從假 success(567字錯誤頁) → 正確 `failed`。
- 部署沿用「唯一 tag 強制全新建置」（避開 `:latest` 快取）：00024→00027。

## 2026-06-14 站台模板擴充 + 模板比對修正 + 尾部裁切 + Tier 2/3 骨架（content-crawler 已部署）
- **Feature（4 個新站台模板）**: 新增 ltn（自由時報，含 news/ec/m 子域）、cna（中央社）、mirrormedia（鏡週刊）、technews（科技新報）。
- **Feature（JSON-LD 萃取）**: 新增 `_extract_from_json_ld()`，從 JSON-LD `NewsArticle.articleBody` 萃取主文（MirrorMedia 等 Next.js styled-components 站台的最可靠來源）。Chrome MCP 實測鏡週刊：JSON-LD 1689 字 = 文章正文，且不含尾部雜訊。
- **Feature（fallback 鏈擴充）**: 主文過短時依序 JSON-LD → block_payload → meta description（原本只有 block_payload）。
- **Fix（模板比對最具體優先）**: 通用 `news` 模板（indicator=`news`）會搶先命中 `cna.com.tw/news/`、`ltn.com.tw/news/` 等網址，蓋掉專屬模板而落入啟發式（抽到 cookie 橫幅）。改為收集所有命中模板，依「具體度」（網域型 indicator 含 `.` 加 1000 權重）排序選最具體者。實測 ltn/cna/nownews/chinatimes/ettoday/mirrormedia/technews 皆正確命中專屬模板。
- **補強（尾部樣板裁切）**: 新增 `_trim_trailing_boilerplate()`，累積 150 字正文後遇到「支持中央社/下載APP/非經授權/一手掌握/點我訂閱/你可能有興趣/支持鏡週刊」等尾部樣板即截斷。保守設計只裁尾部。Chrome MCP 實測 CNA/LTN：正文正確結束，樣板全裁切。
- **Feature（分層爬取 Tier 2/3 骨架）**: 新增 `tiered_fallback.py`：Tier 2（Gemini URL 直讀，env `ENABLE_GEMINI_URL_FALLBACK`）、Tier 3（Webshare 住宅 IP 代理 + proxy auth 擴充，env `WEBSHARE_PROXY_ENABLED`+憑證）。`app.py` `_run_scrape` 改為 Tier 1→2→3 協調器。**全部 env 控制、預設關閉**，未設定時行為與單純 Tier 1 完全相同，等使用者填入憑證才啟用。async 批次（crawl_job.py）暫不走 Tier 2/3（保留 driver 重用），列為後續。
- **文件**: 新增 `crawler-service/CRAWLER_STRATEGY.md`：抽取流程、25+ 站台選擇器對照表、分層策略與 Webshare 成本評估。
- **重構（Tier 2/3 共用化）**：`tiered_fallback.py` 抽出 `run_tier23()`，app.py 與 crawl_job.py 共用；
  async 批次 `/api/crawl/batch` 也接上分層 fallback（Tier 3 用獨立代理 crawler，與重用 driver 分開）。
- ⚠️ **部署踩雷（重要）**：前 3 次以 `--tag gcr.io/.../content-crawler`（隱含 `:latest`）部署，
  Cloud Build 吃到 layer 快取，`COPY . .` 沒帶入新 crawler.py → 線上跑舊碼（log 顯示舊 `Matched template: 'news'`，
  中央社抓到 cookie 橫幅＋相關新聞）。**解法：改用唯一 tag（`specfix-20260614`）強制全新建置**，revision 00024-gvc 才生效。
- ✅ **已部署並線上驗證**：content-crawler revision **00024-gvc**（asia-east1）。X-API-Key 實測 4 站台：
  中央社 1000 字 / 鏡週刊 1689 字（JSON-LD）/ 自由時報 1008 字 / 科技新報 1395 字，皆純正文、尾部樣板已裁。
  log 確認 `Matched template: 'cna' (specificity=1010)` 新碼運行。
- ✅ **已 push GitHub**（main，含本批全部 commit）。

## 2026-06-14 Code-review 修正：5 項 bug／安全問題（deploy-20260614-8，三服務）
- **Fix (crawler dead code)**: `_scroll_and_wait_for_full_load` 的 scrollTo/sleep 移到 return 前，修正 lazy 渲染等待永遠不執行的問題。
- **Fix (RSC regex DOTALL)**: `_extract_from_block_payload` pat1/pat2 加 `re.DOTALL`，修正多行段落在 RSC payload 中被漏抓。
- **Fix (pipeline KeyError)**: `n_intents` 改用 `.get()` 防止 Path 2 完成後計算進度時拋 KeyError。
- **Fix (TF-IDF 單篇)**: 只有一篇文章時 `max_df` 改為 1.0，防止特徵矩陣空白。
- **Fix (header injection)**: download_analysis / download_dataset 的 filename 改用 `re.sub` 清洗，防止 Content-Disposition header 注入。
- **Fix (截斷靜默)**: 文章超過 50,000 字元時現在會顯示 warning flash 訊息。

## 2026-06-14 爬蟲 auto-advance 換頁修正 + adaymag SITE_TEMPLATE（deploy-20260614-5/6/7）
- **Fix（auto-advance URL 換頁）**: `_scroll_and_wait_for_full_load` 新增 `original_url` 參數，每次捲動後偵測 `current_url`，URL 改變時立即停止並回傳 `url_changed=True`。
- **Fix（DOMContentLoaded 快照）**: `_open()` 後立即取 `dom_snapshot_source`（SSR 初始 HTML），URL 換頁時改用快照而非捲動後 DOM，防止 A Day Magazine 等媒體的 auto-advance JS 在 3-7 秒後替換文章內容。
- **新增 SITE_TEMPLATE（adaymag.com）**: `.post-content.entry-content` / `.post-content-container` 直接命中，不須呼叫 Gemini LLM。
- 根本原因確認：透過 Chrome MCP 比對 `friendship-red-flags-unsalvable.html`，發現爬蟲穩定回傳錯誤文章（過度努力/創傷反應），確認是 pushState auto-advance 換頁問題。
- 修正後正確回傳「友情紅旗」友情文章內容。

## 2026-06-14 爬蟲核心邏輯對齊 Colab（Fix C/D/E）
- **Fix C (noise_filter AND化)**: `crawler.py` 噪音關鍵字過濾條件從 `p_count < 5 OR text_len < 800` 改為 `p_count < 3 AND text_len < 400`，對齊 Colab，避免誤刪 Vogue/ELLE 等媒體文章容器。
- **Fix D (_wait_for_content_load)**: 等待 body 後額外輪試 `article`、`main`、`#content`、`.content` 選擇器（各等最多 5 秒），對齊 Colab，確保 JS 渲染完成才抽取 DOM。
- **Fix E (.gitignore REF/)**: 本地 Colab 參考爬蟲資料夾加入 `.gitignore`，不推送 GitHub。
- 部署 content-crawler 至 GCP asia-east1（deploy-20260614-4）。

## 2026-06-14 補強 synthesis + llm_path LLM 呼叫穩健性
- **Fix (synthesis.py)**: §1 摘要、§4 搜尋情境分析、§6 建議各自加個別 try/except，單一章節 LLM 失敗時以佔位文字降級，不中斷其他章節。
- **Fix (llm_path.py)**: `run_qualitative_analysis()` 加 try/except，token 超限或逾時時降級回傳而非中斷整個 Path 2。
- 部署三個服務至 GCP asia-east1（deploy-20260614-3）。

## 2026-06-14 爬蟲新增 nownews、chinatimes、yahoo_tw + ETtoday 修正
- **Feature (爬蟲模板補充)**: 新增 nownews.com（今日新聞）、chinatimes.com（中時新聞網）、yahoo_tw（Yahoo奇摩新聞/財經）三組 SITE_TEMPLATES。
- **Fix (ETtoday redirect)**: ettoday 模板 indicators 加入 `star.ettoday.net`（301 重定向目標），並補充 `#newsContent` 選擇器。
- 部署三個服務至 GCP asia-east1（deploy-20260614-2）。

## 2026-06-14 爬蟲覆蓋率提升 + pipeline 逾時防護 + 輸入驗證
- **Feature (爬蟲模板大幅擴充)**: 新增 12 個台灣媒體 SITE_TEMPLATES：vogue_tw（Condé Nast）、gq_tw、udn（聯合報）、ettoday、thenewslens（關鍵評論網）、gvm（遠見）、bnext（數位時代）、storm_mg（風傳媒）、businesstoday（今周刊）、commonhealth（康健）、cw（天下）。
- **Feature (RSC payload 抽取增強)**: `_extract_from_block_payload` 新增格式 2（React RSC `["$","p","key",{"children":"..."}]`）與格式 3（中文字串 fallback），覆蓋 Vogue/GQ 等 Next.js App Router 頁面。
- **Feature (MAIN_CONTENT_SELECTORS 補強)**: 新增 `.rich-text`、`.prose`、`[class*='richtext']`、`[data-article-body]` 等現代 CMS 選擇器。
- **Fix (噪音過濾放寬)**: 高類別標籤密度過濾條件加嚴（需 p_count < 2 且 density > 1.5），避免誤殺時尚媒體文章容器。
- **Fix (pipeline thread 逾時)**: `t1/t2.join()` 加 `timeout=600`，防止 Path1/2 永久阻塞主執行緒。
- **Fix (content 總長度上限)**: `analysis-service/app.py` 新增 contents 總文字長度 5MB 上限，防止 Firestore 文件超限。
- **Fix (job dict 防禦)**: `_sync_crawling_dataset()` 加 `isinstance(job, dict)` 防止 AttributeError。

## 2026-06-13 部署 deploy-20260613-2（7 項安全修正全部上線）
- 部署三個服務至 GCP asia-east1
- 包含 M2/M5/M6/M7 + M-B1/M-B2/M-B3 共 7 項修正

## 2026-06-13 (M-B1/M-B2/M-B3 第二批安全修正)
- **Fix (M-B1 - URL XSS 防護)**: `analysis-service/report.py` 附錄 URL 輸出前驗證 scheme，僅允許 http/https，防止 `javascript:` scheme 注入 Markdown 連結。
- **Fix (M-B2 - email 查找一致性)**: `app/project_routes.py` `get_user_role()` 改為統一 `email.lower()` 查找，移除雙重 key fallback 邏輯。
- **Fix (M-B3 - 輸入長度截斷 + source_type 白名單)**: `app/project_routes.py` `add_member()` 加 email regex 驗證；`submit_analysis_route()` 對每筆 content 物件強制截斷欄位長度（url 2048、title 512、text 50000）並白名單過濾 source_type。

## 2026-06-13 (M2/M5/M6/M7 安全與穩健性修正)
- **Fix (M2 - Email 大小寫正規化)**: `app/services.py` 的 `get_user`、`ensure_user`、`update_last_login`、`approve_user`、`reject_user` 全加 `email.strip().lower()`，防止同帳號大小寫差異產生重複文件。
- **Fix (M5 - LLM 模型白名單)**: `analysis-service/app.py` 加入 `llm_model` 白名單驗證，僅接受 `gemini-` 或 `claude-` 開頭的模型名，拒絕非法模型注入。
- **Fix (M6 - KMeans 安全上限)**: `analysis-service/nlp_path.py` 加入 `n_clusters = min(n_clusters, len(embeddings))` 限制，並於 embeddings 少於 2 時提前回傳單一分群，防止 Vertex AI 回傳向量不足時 KMeans 崩潰。
- **Fix (M7 - LLM 呼叫逾時)**: `analysis-service/llm_client.py` 加入 300 秒 timeout（透過 `concurrent.futures.ThreadPoolExecutor`），防止 LLM 呼叫永久阻塞分析 pipeline。

## 2026-06-13 (/loop 自動修正：Code Review C1–C5 + 爬蟲模板補強)
- **Security Fix (C3 XSS)**: `analysis_detail.html` 加 DOMPurify.sanitize() 包覆 marked.parse()，防止 LLM 產生的 Markdown 注入惡意 script。
- **Security Fix (C1 SSRF)**: `crawler-service/app.py` v1.4.0 新增 `_is_safe_url()` 過濾函式，攔截私有/保留 IP、loopback、GCP metadata endpoint (169.254.169.254)，套用至三個爬取端點。
- **Fix (C2 LLM JSON 解析)**: `analysis-service/llm_path.py` 新增 `_parse_llm_json()`，用 regex 穩健去除 markdown fence 並抽取 `{...}`，防止 LLM 加說明文字導致整批失敗。`crawler-service/crawler.py` `_ask_gemini_selector()` 同步套用。
- **Fix (C5 scroll timeout)**: `crawler-service/crawler.py` 逾時時先嘗試保留已載入部分內容（≥200 字則降級回傳 `warning`，而非整篇 `failed`）。
- **Fix (M12 retry button)**: 分析失敗頁加「返回專案，重新提交分析」按鈕。
- **Fix (M13 polling timeout)**: 輪詢加 MAX_POLLS=200 逾時上限（約 10 分鐘），停止後提示使用者重新整理。
- **Feature (爬蟲模板)**: SITE_TEMPLATES 新增 elle.com.tw / cosmopolitan.com.tw / harpersbazaar.com.tw（Hearst Asia CMS）、businessweekly.com.tw、parenting.com.tw、cheers.com.tw 六組模板。MAIN_CONTENT_SELECTORS 補充 Hearst `article__body` 系列 class。

## 2026-06-13 (資料集下載 + Gemini 2.5-flash 升級 + 程式碼審查文件)
- **Feature (資料集原始內容下載)**:
    - 已完成爬取的資料集新增下載功能：`/projects/<pid>/datasets/<did>/download.md`（Markdown）及 `/download.json`（JSON）。
    - `project_detail.html` 與 `dataset_detail.html` 新增下載按鈕（僅限 `completed` 狀態資料集顯示）。
    - 新增 `_dataset_to_markdown()`、`_dataset_to_json()`、`_get_completed_dataset_or_redirect()` helper。
    - **修改檔案**: `app/project_routes.py`、`app/templates/project_detail.html`、`app/templates/dataset_detail.html`。
- **Fix (資料集爬取狀態後端同步)**:
    - 使用者離開頁面後，已完成的爬取 job 不再卡在 `crawling` 狀態；`project_detail` 與 `dataset_detail` 頁面載入時呼叫 `_sync_crawling_dataset()` 主動同步。
    - **修改檔案**: `app/project_routes.py`。
- **Fix (Gemini 棄用模型替換)**:
    - `gemini-2.0-flash` 全面替換為 `gemini-2.5-flash`（content-analyser、analysis-service、crawler-service）。
    - crawler-service 後備模型改為 `gemini-2.5-flash-lite`（原為 `gemini-1.5-flash`，已棄用）。
    - **修改檔案**: `analysis-service/app.py`、`analysis-service/llm_client.py`、`analysis-service/pipeline.py`、`app/project_routes.py`、`app/templates/project_detail.html`、`crawler-service/crawler.py`。
- **Docs (程式碼審查 + 前端交接 + 安全事件)**:
    - 新增 `CODE_REVIEW.md`：全專案四模組並行審查，涵蓋 C1 SSRF、C2 LLM JSON 解析、C3 XSS、C4 表格樣式、C5 scroll 逾時，及 M1–M15 中等、L1–L8 低優先問題。
    - 新增 `FRONTEND_HANDOFF.md`：前端重新設計用，含頁面清單、BUG 清單、後端對接點。
    - 新增 `SECURITY_INCIDENTS.md`：記錄 2026-06-13 WebSearch prompt injection 事件及本平台高風險場景分析。

## 2026-06-13 (登入驗證授權修正 + 全站 CSRF + Git 分支流程規範)
- **Security Fix (Broken Access Control)**:
    - **目的**: code review 發現 `main_bp` 與 `project_bp` 各有一份 `login_required`，其中 `main_bp` 版「補查 whitelist 卻不擋非 approved」，導致 pending/rejected 用戶可訪問受保護頁面（如 `/profile`）。
    - **解決方式**:
        1. 新增 `app/auth_guards.py` 作為單一真實來源，統一 `login_required`（含 approved 檢查）與 `is_dev_env()`；`routes.py`、`project_routes.py` 移除各自重複定義改 import。
        2. `project_access_required` 補白名單 gate：被列為某專案 member 的非 approved 用戶不再能繞過白名單。
    - **修改檔案**: 新增 `app/auth_guards.py`；`app/routes.py`、`app/project_routes.py`。
- **Security Fix (資訊洩漏 / session)**:
    - OAuth `callback` 失敗改為記伺服器 log + 通用訊息 + 導回 `/auth`，不再把內部例外字串回傳給使用者。
    - `logout` 改 `session.clear()`，完整清除（含 `whitelist_status` / `_new_api_key` 殘留）。
- **Hardening (CSRF)**:
    - 導入 `Flask-WTF` `CSRFProtect`（`app/__init__.py`），7 個模板 16 個 POST 表單全加 `{{ csrf_token() }}`。
    - 驗證：無 token 的 POST 回 `400 The CSRF token is missing`；帶 token 正常通過。
    - **修改檔案**: `requirements.txt`(+Flask-WTF)、`app/__init__.py`、`app/templates/{profile,project_new,admin_users,project_detail,dataset_detail,admin_api_keys,admin_dashboard}.html`。
- **Process (Git 分支與部署流程)**:
    - CLAUDE.md §2.6 新增標準流程：feature branch 開發 → `核准部署：測試` 部署為 Cloud Run revision tag（不切流量）測試 → 通過才切流量並 `merge --no-ff` 進 main → `核准推送` push。
    - 口令表新增 `核准部署：測試`。`main` 永遠等於已部署且測試通過的穩定版。

## 2026-06-10 21:30:00 (爬蟲拆分為獨立 API 服務 + 對齊 Colab v3.8)
- **Refactor (Architecture)**:
    - **目的**: 將爬蟲改為一個完全獨立、透過 API 操作的 Cloud Run 服務，並以金鑰保護存取。
    - **解決方式**:
        1.  **新增獨立服務 `crawler-service/`**: 自帶 `app.py`(Flask API)、`crawler.py`、`requirements.txt`、`Dockerfile`、`README.md`。
        2.  **API 與金鑰保護**: `POST /api/scrape`（同步單篇）需帶 `X-API-Key`，以 `hmac.compare_digest` 比對環境變數 `CRAWLER_API_KEY`（來自 Secret Manager）；未設定金鑰時一律回 401。另有 `GET /health` 供探活。
        3.  **主程式改用 HTTP 呼叫**: 新增 `app/crawler_client.py`，`app/worker.py` 改呼叫 `scrape_via_api`，移除內嵌爬蟲與 `CURRENT_CRAWLER_INSTANCE`。
        4.  **移除主程式內嵌爬蟲**: 刪除 `app/crawler.py`，並自主程式 `requirements.txt` 移除 `selenium / undetected-chromedriver / selenium-stealth / google-generativeai`；主程式 `Dockerfile` 移除 Chrome 安裝。
    - **修改的程式函式/檔案**: 新增 `crawler-service/{app.py,crawler.py}`、`app/crawler_client.py`；`analysis_pipeline` in `app/worker.py`；`force_kill_crawler` in `app/admin_routes.py`；`Dockerfile`、`deploy.sh`、`requirements.txt`。
- **Feature (Crawler，對齊 Colab v3.8)**:
    - **目的**: 嚴格保留已驗證的 Colab 無頭瀏覽器爬法，只在必要處對齊套件與作法。
    - **解決方式**:
        1.  **UC 初始化修正**: `_init_driver` 統一使用 undetected-chromedriver，移除 Selenium 4 已不支援的 `desired_capabilities`（避免 TypeError），改用 `options.page_load_strategy="eager"`；移除舊的 Nix/標準 Selenium + selenium-stealth 混合分支。
        2.  **OneTrust 同意處理**: `_clear_overlays_and_click_cta` 優先呼叫 `OneTrust.AllowAll()` JS API，失敗才點 `#onetrust-accept-btn-handler` 按鈕。
        3.  **抽取防護**: 新增 `_remove_cmp_containers`，於 `_extract_main_text` 評分前移除 OneTrust/Fides/通用 CMP 容器，避免 cookie 說明被誤判為主文。
        4.  **LLM 套件遷移**: `_ask_gemini_selector` 改用新的 `google-genai`（`genai.Client` + `client.models.generate_content`，`gemini-2.0-flash`，失敗回退 `1.5-flash`）。
    - **保留加值**: `_is_listing_page`、`_scroll_and_wait_for_full_load`、噪音預過濾、`_looks_like_listing_block`、多維度評分與置信度計算等不改核心爬法的功能均保留。
    - **修改的程式函式**: `_init_driver`, `_clear_overlays_and_click_cta`, `_remove_cmp_containers`, `_extract_main_text`, `_ask_gemini_selector`, `configure_genai` in `crawler-service/crawler.py`。

## 2024-05-16
- **Fix**: 修正 `TemplateNotFound` 錯誤，重構專案結構 (`app` package)。
- **Fix**: 修正 Blueprint 註冊與全域變數存取錯誤。

## 2024-05-20
- **Feature**: 專案初始化 (Flask, Bootstrap, `.env`, `requirements.txt`)。

## 2025-12-02
- **Feature**: 實作 Headless Crawler (Selenium, Anti-detection)。
- **Feature**: 實作 Gemini LLM 輔助分析 (Selector prediction)。
- **Feature**: 實作專案制資料持久化 (Firestore: `users/{email}/projects`).
- **Feature**: 實作 Docx 報表匯出 (`python-docx`).
- **Refactor**: 導入 OAuth 身份驗證，移除舊密碼驗證。
- **Deployment**: 建立 Cloud Run 部署配置 (`Dockerfile`, `deploy.sh`) 與 Secret Manager 整合。
- **Fix**: 修復 Firebase Preview 環境登入問題 (Dev Mode Backdoor)。

## 2025-12-05
- **Feature (Crawler)**: 
    - **Log 系統即時化**: 爬蟲內部狀態 (`_log`) 即時回傳至 Firestore，前端可見詳細進度。
    - **Fides/OneTrust 支援**: 移植 Colab 版的高階遮罩處理邏輯 (API 呼叫 + 點擊)。
    - **列表區塊過濾**: 新增 `_looks_like_listing_block` 與評分過濾，避免抓取延伸閱讀。
    - **Gemini 多重比較**: 實作 `_ask_gemini_selector` 回傳多組建議，並與啟發式結果進行評分 PK。
- **UX**: 新增狀態列、停止按鈕、優化頁面佈局。

## 2025-12-05 (Current)
- **Fix (Stability)**: 
    - **問題**: Cloud Run 上發生 `invalid session id` (Chrome Crash)，研判為記憶體不足 (OOM) 與併發衝突。
    - **對策**: 實作 **全局任務鎖 (Global Lock)**，強制同一時間僅允許一個爬蟲任務執行，以時間換取空間與穩定性。
    - **對策**: 強化 `crawler.close()` 資源釋放邏輯。

## 2025-12-09
- **Fix (Crawler)**: 
    - **目的**: 解決 Marie Claire 網站爬取錯誤，避免抓取到「延伸閱讀」區塊。
    - **解決方式**:
        1.  **新增噪音預過濾**: 從 Colab 版本移植了關鍵的噪音過濾邏輯，在內容分析前移除包含 "related", "recommend", "popular" 等關鍵字的元素。
        2.  **強化候選區塊篩選**: 將原本只檢查文字長度的規則，升級為「文字長度 > 300 或段落數 >= 3」，使篩選更精準。
        3.  **確認列表區塊過濾**: 確保在評分前會過濾 `_looks_like_listing_block` 的區塊。
    - **修改的程式函式**: `_extract_main_text` in `app/crawler.py`。
- **Refactor (Architecture)**:
    - **目的**: 完成 Firestore 整合，移除廢棄的記憶體任務儲存。
    - **解決方式**: 確認 `app.config['TASKS']` 未被任何程式邏輯使用後，將其自 `app/__init__.py` 中移除，確認所有任務管理均已由 Firestore 處理。
    - **修改的程式函式**: `create_app` in `app/__init__.py`。
- **Feature (Crawler)**:
    - **目的**: 升級爬蟲評分系統，提高內容抽取的準確性。
    - **解決方式**:
        1.  **移植進階評分邏輯**: 從 Colab 版本的 `_advanced_score_node` 移植了多維度評分系統，綜合考量文本長度、段落品質、連結密度、DOM 深度、視覺權重和中文密度。
        2.  **引入置信度計算**: 新增 `_calculate_confidence` 函式，根據最佳與次佳分數的差距、絕對分數和結構特徵來計算啟發式分析的可信度。
        3.  **重構主文抽取流程**: 修改 `_extract_main_text`，使其採用新的評分與置信度流程，並根據置信度決定是否請求 Gemini LLM 輔助。
    - **修改的程式函式**: `_extract_main_text`, `_calculate_node_score`, `_calculate_confidence`, `_calculate_visual_weight`, `_calculate_dom_depth`, `_calculate_paragraph_quality` in `app/crawler.py`。
- **Fix (Crawler)**:
    - **問題**: 升級評分系統時，因 `replace` 操作失誤，意外移除了 `_looks_like_listing_block` 函式，導致 `AttributeError`。
    - **解決方式**: 重新將 `_looks_like_listing_block` 函式加回 `HeadlessCrawler` class 中。
    - **修改的程式函式**: `_looks_like_listing_block` in `app/crawler.py`。
- **Fix (Crawler)**:
    - **問題**: 爬蟲會錯誤地將文章列表 (`articleList`) 辨識為主文。
    - **解決方式**:
        1.  **強化列表過濾**: 在 `_looks_like_listing_block` 中增加 `'articlelist', 'storylist', 'postlist'` 等關鍵字，以更準確地識別列表區塊。
        2.  **調整評分權重**: 在 `_calculate_node_score` 中，降低「文字長度」的權重 (0.3 -> 0.2)，並提高「連結密度」的懲罰權重 (0.15 -> 0.25)，使其不易被充滿連結的長列表誤導。
    - **修改的程式函式**: `_looks_like_listing_block`, `_calculate_node_score` in `app/crawler.py`。
- **Refactor (Crawler)**:
    - **目的**: 根本性解決「無限滾動列表頁」與「單篇文章頁」的邏輯混淆問題。
    - **解決方式**:
        1.  **引入頁面類型分析**: 新增 `_is_listing_page` 函式，在滾動頁面前，透過檢查多重 `<article>` 標籤等結構特徵，預先判斷頁面是否為列表頁。
        2.  **實現條件執行**: 重構 `scrape` 主函式。若判斷為列表頁，則立即停止處理並回報；若為單篇文章頁，才執行新的 `_scroll_and_wait_for_full_load` 函式以確保內容完整加載。
        3.  **強化除錯日誌**: 在新的判斷與滾動流程中加入詳細的日誌，方便追蹤決策過程。
    - **修改的程式函式**: `scrape`, `_is_listing_page`, `_scroll_and_wait_for_full_load` in `app/crawler.py`。
- **Fix (Crawler)**:
    - **目的**: 修正 `_init_driver` 中的 Python 字典語法錯誤。
    - **解決方式**: 將錯誤的雙大括號 `{{}}` 修正為標準字典語法 `{}`，並補上缺少的右括號 `)`。
    - **修改的程式函式**: `_init_driver` in `app/crawler.py`。

## 2025-12-09 (UC Migration)
- **Feature (Crawler)**:
    - **目的**: 遷移至 `undetected-chromedriver` 以提升 Cloud Run 上的反偵測能力。
    - **解決方式**:
        1.  **Dockerfile 更新**: 增加 `chromedriver` 的自動下載與安裝步驟，確保版本與 `google-chrome-stable` 匹配。
        2.  **Crawler 重構**: 修改 `app/crawler.py`，導入 `undetected_chromedriver`，並移除舊的 Selenium WebDriver 初始化邏輯。特別注意 `headless=new` 與 `version_main` 的設定。
    - **修改的程式函式**: `_init_driver` in `app/crawler.py`, `Dockerfile`.

## 2025-12-11 (Development Environment Fixes)
- **Fix (Dev Environment)**:
    - **目的**: 解決在 Nix 開發環境中 `undetected-chromedriver` 報 `Status code 127` 錯誤的問題。
    - **解決方式**: 
        1.  **實作混合驅動策略**: 修改 `app/crawler.py`，在偵測到 Nix 環境時自動降級為標準 Selenium + `selenium-stealth`，僅在生產環境使用 UC。
        2.  **相依套件更新**: 在 `.idx/dev.nix` 與 `Dockerfile` 中補全 Chrome 運行所需的 Linux 系統函式庫 (`libglib`, `libnss3` 等)。
        3.  **語法修正**: 修正 `.idx/dev.nix` 中的 Nix 列表語法錯誤。
    - **修改的程式函式**: `_init_driver` in `app/crawler.py`, `.idx/dev.nix`.

## 2026-03-06 (Security & Cleanup)
- **Chore (Git)**: 
    - **目的**: 修正因包含金鑰導致 GitHub Push 失敗的問題。
    - **解決方式**: 
        1.  將 `.env` 與 `setup_secret.sh` 加入 `.gitignore`。
        2.  從 Git 索引中移除敏感檔案。
        3.  重建 Git 歷史（force push 單一乾淨 commit）以徹底清除過去 Commit 中的金鑰紀錄。✅

## 2026-06-13 (UI 爬取流程 + 非同步爬蟲 + 金鑰管理)
- **Feature (期A - content-crawler 非同步化)**:
    - **目的**: 讓爬取成為「後端非同步任務 → 產生文件」，UI 與 Colab 都能用標準 API 呼叫。
    - **解決方式**:
        1.  crawler 加入 Firebase Admin（requirements 加 firebase-admin）。
        2.  新增 `POST /api/crawl/batch`（非同步，最多 100 URL，回傳 job_id）與 `GET /api/crawl/{job_id}`。
        3.  背景 thread（crawl_job.py）逐一爬取，進度與結果即時寫入 Firestore `crawl_jobs/{job_id}`。
        4.  保留同步 /api/scrape、/api/scrape/batch 向後相容。版本升至 1.3.0。
    - **新增檔案**: crawler-service/crawl_job.py、auth.py。

- **Feature (期C - 完整 api_keys 金鑰管理)**:
    - **目的**: 系統管理員可核發/撤銷供 Colab、Claude Cowork 使用的 API 金鑰。
    - **解決方式**:
        1.  `app/services.py` 新增 create/list/revoke/reactivate_api_key；金鑰明文只顯示一次，Firestore 只存 SHA-256 hash。
        2.  金鑰含 permissions（crawl / analyse），驗證時檢查。
        3.  crawler 與 analysis 各自的 `auth.is_authorized` 升級：先比對 Secret Manager 系統金鑰，再查 `api_keys` 白名單（hash + is_active + permission），並更新 last_used/call_count。
        4.  Admin UI：`/admin/api-keys` 核發、列表、撤銷、重新啟用 + Colab 呼叫範例。
    - **新增檔案**: app/templates/admin_api_keys.html、crawler-service/auth.py、analysis-service/auth.py。

- **Feature (期B - UI 爬取資料集流程)**:
    - **目的**: Project 內可直接輸入網址 → 後端非同步爬取成「資料集文件」→ 一鍵送分析。
    - **解決方式**:
        1.  `app/crawler_client.py` 新增 submit_crawl_batch / get_crawl_status。
        2.  `app/project_routes.py` 新增 datasets 路由：建立（提交爬取）、輪詢 status（完成時同步 crawler 結果進 dataset.items）、檢視、一鍵 analyse。
        3.  資料模型：`projects/{pid}/datasets/{did}`（name, source_urls, crawl_job_id, status, items 等）。
        4.  project_detail 改為三段式（① 爬取資料集 → ② 一鍵分析 → 進階 JSON 摺疊）；新增 dataset_detail.html（進度條 + 結果表 + 一鍵分析）。

## 2026-06-12 (正式部署 + 品牌命名 InsightOut)
- **Deploy (生產上線)**:
    - **目的**: 將三服務首次部署至 Google Cloud Run（GCP Project: content-analyser-cn）。
    - **過程與踩雷**:
        1.  **Debian trixie 套件問題**: `python:3.11-slim` 已升為 Debian 13，移除 `libgconf-2-4`，導致 crawler 建置失敗。鎖定三服務 base image 為 `python:3.11-slim-bookworm`。
        2.  **gcloud 旗標衝突**: content-analyser 部署同時用 `--clear-env-vars` 與 `--set-env-vars`，gcloud 不允許並存，移除前者。
    - **部署結果**: 三服務全部上線，health check 通過。
        - content-analyser: https://content-analyser-dha6qmuvaq-de.a.run.app
        - content-crawler: Chrome 149 安裝成功
        - analysis-pipeline: Firebase 連線正常
    - **環境設定**:
        1.  啟用 Vertex AI（aiplatform.googleapis.com）。
        2.  建立 Secret Manager: CRAWLER_API_KEY、ANALYSIS_API_KEY。
        3.  管理員 email（how.penguin@gmail.com）寫入 Firestore system/config（REST API PATCH）。
        4.  OAuth 重新導向 URI 加入兩個 .run.app callback + insightout.annexix.cc callback。
    - **端到端驗證**: Google OAuth 登入實測通過，管理員直接進入專案頁。
- **Branding (產品命名)**:
    - **目的**: 確立正式產品名與網址。
    - **解決方式**: 命名為 **InsightOut**（insight + inside-out），正式網址 insightout.annexix.cc。
        更新 layout/login 模板品牌、product_guideline.md v1.5、刪除舊 index.html、清空舊 app.js。
- **Domain (網域，進行中)**:
    - annexix.cc 已透過 Google Search Console + Cloudflare 一鍵授權完成網域驗證。
    - Cloud Run domain mapping 已建立（要求 CNAME: insightout → ghs.googlehosted.com）。
    - **待辦**: Cloudflare CNAME 記錄尚未加（Cloudflare dashboard 自動化受限），加完等 SSL 憑證配發後 insightout.annexix.cc 生效。
- **Known Issue**:
    - 登入後 navbar 右上角仍顯示「登入」而非用戶帳號（context_processor / 模板顯示邏輯問題，不影響功能，待修）。

## 2026-06-12 (Phase 2 + Phase 3 + Phase 4)
- **Feature (Phase 2 - analysis-pipeline)**:
    - **目的**: 建立全新的獨立分析引擎，實現雙路平行 + Synthesis 架構。
    - **解決方式**:
        1.  **新增 `analysis-service/`**: 獨立 Cloud Run 服務。
        2.  **Path 1（nlp_path.py）**: TF-IDF（jieba + scikit-learn）+ Vertex AI text-multilingual-embedding-002 語意向量 + KMeans 分群。
        3.  **Path 2（llm_path.py）**: 逐批搜尋意圖萃取 + 跨文章六面向質化分析。
        4.  **Synthesis（synthesis.py）**: 整合兩路輸出，生成摘要、搜尋情境分析、可操作建議。
        5.  **報告組裝（report.py）**: TF-IDF 表格與語意群組由程式生成，LLM 負責詮釋章節。
        6.  **LLM 抽象層（llm_client.py）**: 統一 Gemini / Claude 呼叫介面。
        7.  **非同步 API**: `POST /api/analyse` 回傳 job_id，`GET /api/analyse/{job_id}` 輪詢，任務狀態存 Firestore `analysis_jobs/{job_id}`。
    - **修改的程式函式**: 新增 `run_analysis`(pipeline.py), `run`(nlp_path/llm_path/synthesis), `assemble`(report.py), `LLMClient`(llm_client.py)。

- **Feature (Phase 3 - 控制平面)**:
    - **目的**: 將 content-analyser 重構為完整控制平面 + Project 協作 Web UI。
    - **解決方式**:
        1.  **白名單流程**: `ensure_user()` 首次登入建立 pending 用戶；callback 判斷狀態；`/pending` 頁面。
        2.  **Project 管理（project_routes.py）**: 建立/設定/成員管理，Owner/Editor/Viewer 三級權限。
        3.  **分析任務**: 提交內容給 analysis-pipeline、進度輪詢、報告檢視（marked.js 渲染）、下載 .md。
        4.  **Admin 控制台**: 服務健康監控、白名單審核、Secret Manager 金鑰管理。
        5.  **新增 `analysis_client.py`**: 分析服務 HTTP 客戶端。
        6.  **新增 7 個 Jinja2 模板**：projects, project_new, project_detail, analysis_detail, pending, admin_users, 重寫 admin_dashboard。
    - **修改的程式函式**: 新增 `ensure_user`, `approve_user`, `list_all_users`(services.py)；全部路由 in `project_routes.py`、`admin_routes.py`。

- **Chore (Phase 4 - 整合收尾)**:
    - **目的**: 修正白名單漏洞、更新文件、補齊環境變數範本。
    - **解決方式**:
        1.  **修正白名單 session 漏洞**: `login_required` 在 session 缺少 whitelist_status 時從 Firestore 補查，避免舊 session 繞過審核。
        2.  **更新 `CLAUDE.md` 至 v3.0**: 附錄 A–E 改為三服務架構、新 Firestore schema、新環境變數。
        3.  **新增 `.env.example`**: 本地開發環境變數範本。
    - **修改的程式函式**: `login_required` in `app/routes.py`, `app/project_routes.py`。

## 2026-06-12 (Phase 0 + Phase 1)
- **Chore (Phase 0 - 清理地基)**:
    - **目的**: 移除所有架構錯誤的舊設計，為新架構打好基礎。
    - **解決方式**:
        1.  移除 `CRAWLER_LOCK`（全域鎖在微服務架構無意義）與 `analysis_pipeline()`（主程式不再協調爬蟲）。
        2.  移除 hardcode `ADMIN_EMAIL`，改為 `get_admin_email()` 從 Firestore `system/config` 讀取。
        3.  刪除 `app/export_utils.py`（輸出改為 Markdown）。
        4.  精簡 `app/crawler_client.py` 為 health check only。
        5.  `requirements.txt` 移除 `beautifulsoup4`、`lxml`、`python-docx`。
        6.  修正 `devserver.sh` shebang 與 PORT 預設值。
        7.  新增 `setup_admin.sh.example` 與 `app/services.py` 的 `get_admin_email()`。
        8.  `/submit_task`、`/task_status`、`/stop_task`、`/download_project` 改為 503 stub。
    - **修改的程式函式**: `analysis_pipeline`, `CRAWLER_LOCK` in `app/worker.py`（移除）；`get_admin_email` in `app/services.py`（新增）；`admin_required` in `app/admin_routes.py`；全部路由 in `app/routes.py`。

- **Feature (Phase 1 - 爬蟲補強，對齊 Colab v3.8)**:
    - **目的**: 補強 `content-crawler` 的穩健性，對齊已驗證的 Colab v3.8 實作。
    - **解決方式**:
        1.  **新增 `UnsupportedSiteError`**: 不支援的網站（如 Dcard）直接拋出，呼叫端視為 `status=skipped`。
        2.  **Dcard 跳過**: `scrape()` 開頭偵測 `dcard.tw`，直接回傳 skipped（需登入，改用 Chrome MCP）。
        3.  **新增 `_open()` 重試邏輯**: 最多 2 次重試，含逾時偵測與 `window.stop()` 重置（對齊 Colab）。
        4.  **每頁硬性時限**: `scrape()` 加入 `hard_timeout_sec=60` 參數，在載入、遮罩、滾動後各做 deadline 檢查。
        5.  **新增 `_apply_meta_fallback()`**: 主文 < 200 字時補入 `og:description` / `meta[name=description]`（對齊 Colab）。
        6.  **`crawler-service/app.py` 版本升級為 1.2.0**：`/api/scrape` 支援呼叫端自訂 `hard_timeout_sec`。
    - **修改的程式函式**: `scrape`, `_open`, `_apply_meta_fallback`, `UnsupportedSiteError` in `crawler-service/crawler.py`；`_run_scrape`, `/api/scrape` in `crawler-service/app.py`。

## 2026-06-12 (Crawler Microservice)
- **Refactor (Architecture)**:
    - **目的**: 將爬蟲從主程式內嵌架構，拆分為完全獨立的 Cloud Run 微服務（`content-crawler`），使其可被任何外部系統（Colab、Claude Cowork 等）呼叫。
    - **解決方式**:
        1.  **新增 `crawler-service/`**: 獨立 Flask API 服務，包含 `app.py`（API 入口）、`crawler.py`（爬蟲核心）、`Dockerfile`（含 Chrome 安裝）、`requirements.txt`。
        2.  **API 端點**: `GET /health`（探活）、`POST /api/scrape`（單一 URL）、`POST /api/scrape/batch`（批次，最多 20 個 URL）。所有 `/api` 端點以 `X-API-Key` 保護（`hmac.compare_digest` 防 timing attack）。
        3.  **新增 `app/crawler_client.py`**: 主程式 HTTP 客戶端，提供 `scrape_via_api()` 與 `scrape_batch_via_api()` 兩個函式。
        4.  **更新 `app/worker.py`**: 改用 `crawler_client` 透過 HTTP 呼叫，不再內嵌 Chrome。
        5.  **精簡主程式 `Dockerfile`**: 移除所有 Chrome / ChromeDriver 安裝，映像大幅縮小。
        6.  **更新 `deploy.sh`**: 先部署 `content-crawler`，取得其 URL 後注入主程式並部署 `content-analyser`。
    - **修改的程式函式**: `analysis_pipeline` in `app/worker.py`；新增 `scrape_via_api`, `scrape_batch_via_api` in `app/crawler_client.py`；新增 `scrape`, `scrape_batch`, `health`, `_run_scrape` in `crawler-service/app.py`。
- **Refactor (Crawler Core)**:
    - **目的**: 對齊已驗證的 Colab v3.8 爬法，修正累積的技術債。
    - **解決方式**:
        1.  移除 Selenium 4 已廢棄的 `desired_capabilities`，改用 `options.page_load_strategy = "eager"`。
        2.  OneTrust 遮罩優先呼叫 `OneTrust.AllowAll()` JS API（失敗才點按鈕）。
        3.  主文抽取前移除整個 OneTrust / Fides CMP 容器（避免 cookie 說明被誤判為主文）。
        4.  LLM 選擇器輔助從舊版 `google-generativeai` 遷移至新版 `google-genai`（`genai.Client` 寫法）。
        5.  統一使用 `undetected-chromedriver`，移除 Nix 環境 selenium-stealth 混合分支（爬蟲服務僅在 Cloud Run 執行）。
    - **修改的程式函式**: `_init_driver`, `configure_genai`, `_ask_gemini_selector`, `_remove_cmp_containers`, `_clear_overlays_and_click_cta` in `crawler-service/crawler.py`。
- **Docs**:
    - **目的**: 建立標準技術文件，供 Claude Code 協作使用。
    - **解決方式**: 新增 `CLAUDE.md`，記錄架構、API 規格、Firestore schema、環境變數清單、常用指令與開發規範。
