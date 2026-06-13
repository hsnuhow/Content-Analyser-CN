# 爬蟲策略與技術發現（Crawler Strategy & Findings）

**服務：** content-crawler（Cloud Run，4Gi，Chrome + undetected-chromedriver）
**版本基準：** Colab v3.8 對齊版
**最後更新：** 2026-06-14

本文件記錄爬蟲服務的設計策略、各站台選擇器、抽取 fallback 鏈、以及分層爬取（含付費代理）的規劃與成本評估。

---

## 1. 核心抽取流程

```
_open(url)  ── DOMContentLoaded（eager 策略）
   │
   ├─ dom_snapshot_source = page_source   ⭐ 立即取快照（防 auto-advance JS 換頁）
   │
_wait_for_content_load()  ── 等 body + 主內容元素（article/main/#content）
   │
_clear_overlays_and_click_cta()  ── 遮罩處理：OneTrust → Fides → 通用後備
   │
_is_listing_page()  ── 列表頁判斷（多個 <article> 或 article-like <li> → skip）
   │
_scroll_and_wait_for_full_load(original_url=url)  ── 滾動觸發 lazy-load
   │                                                   ⭐ 偵測 URL 換頁（pushState）
   │
   ├─ url_changed？ → 用 dom_snapshot_source（初始正確文章）
   └─ 正常 → page_source（滾動後完整內文）
   │
_extract_main_text()  ── 模板選擇器 → 噪音過濾 → 候選評分 → 置信度 → LLM 輔助
   │
主文 < 500 字 fallback 鏈：
   1) _extract_from_json_ld()      ── JSON-LD NewsArticle.articleBody（MirrorMedia 等）
   2) _extract_from_block_payload() ── Next.js RSC / Copilot 序列化 payload
   3) _apply_meta_fallback()        ── og:description / meta description
```

---

## 2. 為什麼 Colab 正常、Cloud Run 異常？（已解決）

| 差異點 | Colab | Cloud Run | 修正 |
|--------|-------|-----------|------|
| **地理位置** | 視 runtime 而定，常為美國/亞洲 | asia-east1（台灣） | `--accept-lang` + CDP timezone override |
| **auto-advance JS** | 互動式環境，人工觀察可中斷 | headless，數秒後 DOM 被替換 | DOMContentLoaded 後立即取 `dom_snapshot_source` |
| **無限捲動換頁** | 較少觸發 | 自動捲動觸發 pushState 換到下一篇 | 捲動時偵測 `current_url != original_url`，停止並回退快照 |
| **RSC 多行段落** | 同 regex | 同 regex | 加 `re.DOTALL` 讓段落跨行匹配 |
| **冷啟動** | 已預熱 | undetected-chromedriver 初始化 40–50s | deadline 在 driver init 後才計時 |

**結論：** 根本原因是現代媒體站（A Day Magazine、Condé Nast 系）的 **client-side 內容替換** 與 **pushState 換頁**，在 headless 環境下會抓到錯誤文章。已透過「初始快照 + URL 換頁偵測」解決。

---

## 3. 站台選擇器對照表

> 模板比對在噪音過濾「之前」執行（Phase 2.0），命中即優先。
> `indicators` 比對 URL，`selectors` 依序嘗試，第一個 ≥300 字者勝出並快取。

### 時尚/生活媒體

| 站台 | indicator | 主選擇器 | 備註 |
|------|-----------|---------|------|
| A Day Magazine | adaymag.com | `.post-content.entry-content` | WordPress + auto-advance，需快照 |
| Marie Claire | marieclaire.com | `.articleContent` | |
| ELLE 台灣 | elle.com.tw | `.article__body-content` | Hearst CMS |
| Cosmopolitan 台灣 | cosmopolitan.com.tw | `.article__body-content` | Hearst CMS |
| Harper's Bazaar 台灣 | harpersbazaar.com.tw | `.article__body-content` | Hearst CMS |
| Vogue 台灣 | vogue.com.tw | `[class*="ArticleBody"]` | Condé Nast Next.js + OneTrust |
| GQ 台灣 | gq.com.tw | `[class*="ArticleBody"]` | Condé Nast Next.js + Fides |
| she.com | she.com | `.content-detail.expand` | |

### 新聞媒體

| 站台 | indicator | 主選擇器 | 備註 |
|------|-----------|---------|------|
| 自由時報 | ltn.com.tw | `.article_body` / `.text` | JS 渲染，多子域（news/ec/m）|
| 中央社 | cna.com.tw | `article.article` | 靜態 HTML 有全文 |
| 鏡週刊 | mirrormedia.mg | `[class*="ArticleBody"]` + JSON-LD | Next.js styled-components |
| 聯合報 | udn.com | `.article-body__editor` | |
| ETtoday | ettoday.net | `.story` / `#story` | 可能 Cloudflare |
| 今日新聞 | nownews.com | `#article_content` | |
| 中時 | chinatimes.com | `.article-body` | |
| Yahoo 新聞 | tw.yahoo.com | `.caas-body` | |
| 關鍵評論網 | thenewslens.com | `.main-content` | 部分付費牆 |
| 風傳媒 | storm.mg | `.article-body` | 部分付費牆 |
| TechNews 科技新報 | technews.tw | `.entry-content` | WordPress |

### 商業/財經/健康

| 站台 | indicator | 主選擇器 |
|------|-----------|---------|
| 遠見 | gvm.com.tw | `.article_body` |
| 數位時代 | bnext.com.tw | `.article-content__editor` |
| 今周刊 | businesstoday.com.tw | `.article-content` |
| 康健 | commonhealth.com.tw | `.article-body` |
| 天下 | cw.com.tw | `.article-body` |
| 商業週刊 | businessweekly.com.tw | `.article-body` |
| 親子天下 | parenting.com.tw | `.article-body` |
| Cheers | cheers.com.tw | `.article-body` |

### CMS 通用模板

| 模板 | indicator | 用途 |
|------|-----------|------|
| wordpress | wp-content/wp-includes | WordPress 站台通用 |
| pixnet | pixnet.net | 痞客邦 |
| news | news/article/story | 一般新聞站通用後備 |

---

## 4. 分層爬取策略（Tiered Crawling）

針對「反爬蟲擋下」的網址，採用**成本遞增、僅對失敗者升級**的三層策略：

```
┌─ Tier 1：undetected-chromedriver（現有，零額外成本）
│    無頭 Chrome + 反偵測。涵蓋 95%+ 一般站台。
│        │ status=failed 或 content < 300 字
│        ▼
├─ Tier 2：Gemini URL 直讀（低成本，僅 token）
│    將 URL 交給 Gemini grounding / URL context，請其回傳正文。
│    對純 SSR、輕反爬站台有效；無需啟動瀏覽器。
│        │ 仍失敗
│        ▼
└─ Tier 3：住宅 IP 代理（Webshare，付費）
     undetected-chromedriver + 住宅 IP，繞過 Cloudflare Bot Management /
     資料中心 IP 封鎖。僅對確認失敗的網址啟用，控制費用。
```

### 設計原則

1. **只對失敗升級**：成功的網址永不進入付費層。
2. **可關閉**：Tier 2/3 由環境變數控制（`ENABLE_GEMINI_FALLBACK`、`ENABLE_PROXY_FALLBACK`），預設關閉。
3. **失敗清單可追溯**：每次爬取記錄使用到哪一層，便於成本歸因與優化選擇器。
4. **不適用清單**：需登入（Dcard）、硬付費牆（壹蘋果、部分天下/風傳媒深度文）即使住宅 IP 也無法突破，直接 skip。

### Webshare 接入方式（Tier 3）

```python
# undetected-chromedriver 接入 proxy（含帳密）
options.add_argument(
    f'--proxy-server=http://{WEBSHARE_USER}:{WEBSHARE_PASS}@{WEBSHARE_HOST}:{WEBSHARE_PORT}'
)
# 註：Chrome 對含帳密的 --proxy-server 支援有限，正式實作建議用
#     selenium-wire 或 proxy extension 處理 proxy authentication。
```

---

## 5. 成本評估（Webshare）

| 項目 | 數值 |
|------|------|
| Webshare 住宅 IP 計價 | 約 **USD $3–7 / GB**（依方案，以美金計價）|
| 台灣媒體單頁流量 | 約 0.5–2 MB（含圖片；可設 `--blink-settings=imagesEnabled=false` 降流量）|
| 1000 篇文章估算 | 約 1–2 GB → **USD $3–14** |
| 若僅 10% 需 Tier 3 | 約 0.1–0.2 GB → **< USD $1.5** |

**關鍵：** 因只對失敗網址啟用 Tier 3，實際費用遠低於全量代理。停用圖片載入可再降 60–80% 流量。

> ⚠️ Webshare 以**美金**計價、按流量（GB）或按 IP 數收費，非台幣。住宅方案（Residential）按流量；靜態資料中心方案（Static Datacenter / ISP）按 IP 月租。反爬蟲繞過需用 **Residential**。

---

## 6. 已知限制

| 站台類型 | 範例 | 處理方式 |
|---------|------|---------|
| 需登入 | Dcard | 直接 skip（程式內建判斷）|
| 硬付費牆 | 壹蘋果、部分深度報導 | 抓到導語即回，標記 partial |
| 強 Bot 偵測 | 部分 Cloudflare 站 | 升級 Tier 3 住宅 IP |
| 純圖片/影音 | 純 IG/YT 嵌入頁 | meta description fallback |

---

## 7. 後續優化方向

1. **選擇器自我修復**：當某站台連續 N 次落入啟發式（未命中模板），自動觸發 Gemini 選擇器建議並回寫模板候選。
2. **流量優化**：Tier 3 啟用時預設關閉圖片載入。
3. **失敗統計儀表板**：在 admin 後台呈現各站台成功率，指引選擇器維護優先序。
4. **proxy 輪換池**：Webshare 多 IP 輪換，降低單一 IP 被封風險。
