# Changelog

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

## 2026-06-15 修正：全面 code review + 安全審查問題修正（待部署四服務）
完整審查（5 路並行深審 + 驗證）後，修正所有 Critical/High 與多數 Medium 問題。分 5 批提交：
- **安全批 1**：SSRF（crawler `_is_safe_url` 對 domain 解析 DNS、任一 IP 落內網即拒，補「域名指向 metadata」繞過）；
  SECRET_KEY 正式環境 fail-fast；安全標頭（CSP/X-Frame-Options/nosniff/HSTS）；/debug 非 dev 回 404；前端 bootstrap/marked/dompurify 鎖版+SRI。
- **安全+韌性批 2（analysis）**：prompt_safety（INJECTION_GUARD + `<DATA>` 包裹）防爬取內容注入 LLM；
  llm_client 對 429/5xx 指數退避重試、Claude 回應防呆、Gemini fallback 收窄；Path1 逾時 race 修正（丟棄部分結果+凍結 search_extent 快照）；失敗批次顯性化；report §3 連結 scheme 白名單。
- **邏輯批 3（content-analyser）**：爬蟲續批副作用 gate 給 editor+（Viewer 唯讀輪詢不再 spawn）+ 交易式認領防雙開重複續批；
  analyse_dataset 補 100 篇上限+50k 截斷；_save_dataset_items _seq 交易式預約防併發碰撞；list_models 只准查相符 provider 防金鑰外洩；OAuth email 正規化小寫。
- **穩健批 4**：crawler hard_timeout 夾值、學習選擇器驗證、crawler/analysis cleanup 改 where 過濾+limit；nlp_path embeddings 對位降級；search_extent_client 非 2xx 一律 error。
- **納入開發程序**：較大項（crawler redirect SSRF 殘留 / 同步看門狗 / close 超時、list_projects 全集合掃描）拆背景任務並記於 OPTIMIZATION.md；search-extent §7 e2e 阻塞於 Ads token。
- 全部 py_compile 通過；尚未部署（待部署四服務皆 image-only；content-analyser 需先確認 SECRET_KEY 已注入，否則 fail-fast 會擋啟動）。

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

## 2026-06-14 新增第 4 微服務 search-extent（B1：Ads Keyword Planner 關鍵字延伸，待部署）
延伸服務 B 第一階段。獨立 Cloud Run 服務，尚未部署，等 Google Ads API Basic access 核准補 dev token。

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

## 2026-06-14 資料治理四features：刪除/更名 + 孤兒清理 + 強制停止 + usage_log + LLM 精緻調配（待部署）
四項一次開發，尚未部署。涉及三服務（新增取消/清理 API → 需重新部署 crawler 與 analysis）。

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
