# -*- coding: utf-8 -*-
"""
獨立爬蟲服務核心 (HeadlessCrawler)

核心爬取邏輯嚴格對齊已驗證的 Colab v3.8：
  - 統一使用 undetected-chromedriver（移除舊的 Nix / selenium-stealth 混合分支）。
  - _init_driver 移除 Selenium 4 不支援的 desired_capabilities，改用
    options.page_load_strategy = "eager"。
  - 遮罩處理優先呼叫 OneTrust.AllowAll() JS API（失敗才點按鈕），其次處理 Fides，
    最後才走通用點擊後備邏輯。
  - 主文抽取前先移除 OneTrust / Fides / 通用 CMP 容器，避免 cookie 說明被誤判為主文。
  - LLM 選擇器輔助改用新的 google-genai 套件（genai.Client + models.generate_content）。

並保留 Cloud Run 既有的加值（不改變核心爬法）：
  - 列表頁判斷 _is_listing_page、完整滾動 _scroll_and_wait_for_full_load。
  - 噪音預過濾、_looks_like_listing_block、多維度評分與置信度計算。
"""
import os
import re
import time
import json
import traceback
from typing import Optional, Tuple, Dict, Any, List, Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup
# 統一使用 undetected-chromedriver（對齊 Colab，最佳反偵測）
import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# LLM Support：對齊 Colab v3.8，改用新的 google-genai 套件
# （舊的 google-generativeai 已停止維護，API 寫法不同）
try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

# 常數設定
ZH_ACCEPT_LANGUAGE = "zh-TW,zh;q=0.9,en;q=0.5"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

MASK_BUTTON_PATTERNS = [
    r"同意|接受|我知道|允許|關閉|略過|跳過|進入|是|已滿\s*18|我已滿\s*18",
    r"OK|Accept|Agree|Allow|Continue|Close|Got\s*it|Skip|Enter|Yes",
]

OVERLAY_SELECTORS = [
    "[aria-modal=\"true\"]",
    "[role=dialog]",
    "div[class*=modal]",
    "div[class*=overlay]",
    "div[class*=cookie]",
    "div[id*=cookie]",
    "div[class*=consent]",
    "div[class*=gdpr]",
]

MAIN_CONTENT_SELECTORS = [
    "article", "main", "[role=main]", ".content", "#content",
    ".post-content", ".article-content", ".entry-content", ".post-body",
    "[class*=content]", "[class*=article]", "[class*=post]",
    ".article-body", "[itemprop=articleBody]", ".story__content",
    ".content-detail.expand", "#container .content-left .content-detail",
    # Hearst Asia CMS（ELLE / Cosmo / Bazaar）
    ".article__body-content", ".article__body", ".article-body-content",
    ".article-text", "[class*='article__body']",
    # 常見中文媒體
    ".single-content", ".content-body", ".post-article",
    "[class*='entry-body']", "[class*='article-text']",
    # 現代 CMS / styled-components / Tailwind
    ".rich-text", ".richtext", "[class*='rich-text']", "[class*='richtext']",
    ".prose", "[class*='prose']",
    ".body-text", ".body-copy", "[class*='body-text']",
    "[class*='story-body']", "[class*='story-content']",
    # 資料屬性選擇器
    "[data-content-type='article']", "[data-article-body]", "[data-module='article-body']",
]

SITE_TEMPLATES = {
    # ── A Day Magazine（WordPress + #infinite-article 無限捲動換頁）──
    # 注意：頁面使用 auto-advance JS，數秒後自動替換 DOM 並改 URL（pushState）
    # 必須搭配 dom_snapshot_source 才能取到正確文章
    'adaymag': {
        'indicators': ['adaymag.com'],
        'selectors': [
            '.post-content.entry-content', '.post-content-container',
            'article.blog-post .entry-content', 'article .entry-content',
            '.entry-content', '.post-content', 'article',
        ]
    },
    'wordpress': {
        'indicators': ['wp-content', 'wp-includes', 'wordpress'],
        'selectors': ['.entry-content', '.post-content', 'article .content', '.single-content']
    },
    'pixnet': {
        'indicators': ['pixnet.net', 'pixnet'],
        'selectors': ['#article-content', '.article-content-inner', '#article-body']
    },
    'news': {
        'indicators': ['news', 'article', 'story'],
        'selectors': ['.article-body', '.story-body', '[itemprop=articleBody]', '.post-content']
    },
    'she': {
        'indicators': ['she.com'],
        'selectors': ['.content-detail.expand', '.content-detail', '.article-content']
    },
    'marieclaire': {
        'indicators': ['marieclaire.com'],
        'selectors': [
            '.articleContent',
            'div.articleContent',
            '[id^="content"]',
            '#container80407 .articleContent',
            '.articleContainer .articleContent',
            '.article-content',
            'article .content',
            '[class*="article"][class*="content"]',
            '.post-content',
            '[itemprop="articleBody"]',
            'main article',
            'article'
        ]
    },
    # ── Hearst Asia CMS（ELLE / Cosmopolitan / Harper's Bazaar 台灣版）──
    # 均使用同一套 Hearst Digital CMS，class 命名一致。
    # 注意：ELLE/Cosmo/Bazaar 台灣為 HTTP-only 站（Fastly nonssl 端點，https 連線失敗）。
    # Hearst 新版 CMS 主文容器為 .listicle-body-content / .content-container / [class*=body-content]，
    # 舊版為 .article__body-content（保留為 fallback）。
    'elle_tw': {
        # elle.com.tw = 台灣站（HTTP-only）；elle.com/tw = Hearst 國際站台灣版（HTTPS）。兩者同 CMS。
        'indicators': ['elle.com.tw', 'elle.com/tw'],
        'selectors': [
            '.standard-article-content',
            '.listicle-body-content',
            '[class*="body-content"]',
            '.content-container',
            '.article__body-content',
            '.article__body',
            '.article-content',
            '.article-body-content',
            '.article-body',
            '.article-text',
            '[class*="article__body"]',
            '[class*="article-body"]',
            '[itemprop="articleBody"]',
            'article .content',
            'article',
        ]
    },
    'cosmopolitan_tw': {
        'indicators': ['cosmopolitan.com.tw', 'cosmo.com.tw', 'cosmopolitan.com/tw'],
        'selectors': [
            '.standard-article-content',
            '.listicle-body-content',
            '[class*="body-content"]',
            '.content-container',
            '.article__body-content',
            '.article__body',
            '.article-content',
            '.article-body',
            '[class*="article__body"]',
            '[itemprop="articleBody"]',
            'article',
        ]
    },
    'harpersbazaar_tw': {
        'indicators': ['harpersbazaar.com.tw', 'harpersbazaar.com/tw'],
        'selectors': [
            '.standard-article-content',
            '.listicle-body-content',
            '[class*="body-content"]',
            '.content-container',
            '.article__body-content',
            '.article__body',
            '.article-content',
            '.article-body',
            '[class*="article__body"]',
            '[itemprop="articleBody"]',
            'article',
        ]
    },
    # ── Condé Nast 台灣（Vogue / GQ）── Next.js App Router + styled-components
    'vogue_tw': {
        'indicators': ['vogue.com.tw'],
        'selectors': [
            '[class*="ArticleBody"]', '[class*="article-body"]',
            '[class*="RichText"]', '[class*="richtext"]',
            '[class*="ContentBody"]', '[class*="StoryBody"]',
            '.article-content', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    'gq_tw': {
        'indicators': ['gq.com.tw'],
        'selectors': [
            '[class*="ArticleBody"]', '[class*="article-body"]',
            '[class*="RichText"]', '[class*="richtext"]',
            '[class*="ContentBody"]', '[class*="StoryBody"]',
            '.article-content', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 聯合報 (udn.com) ──
    'udn': {
        'indicators': ['udn.com'],
        'selectors': [
            '.article-body__editor', '.article-content__wrapper',
            '#story_body_content', '.article-content',
            '[class*="article-body"]', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── ETtoday 新聞雲（ettoday.net → star.ettoday.net redirect）──
    'ettoday': {
        'indicators': ['ettoday.net', 'star.ettoday.net'],
        'selectors': [
            '.story', '#story', '.story-details',
            '.newsContent', '#newsContent',
            '.article-content', '.article-body',
            '[class*="story"]', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 今日新聞 (nownews.com) ──
    'nownews': {
        'indicators': ['nownews.com'],
        'selectors': [
            '#article_content', '.article_body', '.article-body',
            '.article-content', '.content-body',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 中時新聞網 (chinatimes.com) ──
    'chinatimes': {
        'indicators': ['chinatimes.com'],
        'selectors': [
            '.article-body', '.article-box',
            '.article-content', '#article-body',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── Yahoo奇摩新聞 / Yahoo Finance ──
    'yahoo_tw': {
        'indicators': ['yahoo.com/news', 'tw.yahoo.com', 'tw.finance.yahoo.com'],
        'selectors': [
            '[class*="caas-body"]', '.caas-body',
            '.article-content', '[data-component="text-block"]',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 關鍵評論網 (thenewslens.com) ──
    'thenewslens': {
        'indicators': ['thenewslens.com'],
        'selectors': [
            '.main-content', '[class*="article-body"]',
            '.article-content', '.content-body',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 遠見 (gvm.com.tw) ──
    'gvm': {
        'indicators': ['gvm.com.tw'],
        'selectors': [
            '.article_body', '.article-body', '.content-body',
            '.article-content', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 數位時代 (bnext.com.tw) ──
    'bnext': {
        'indicators': ['bnext.com.tw'],
        'selectors': [
            '.article-content__editor', '.article-content',
            '.post-content', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 風傳媒 (storm.mg) ──
    'storm_mg': {
        'indicators': ['storm.mg'],
        'selectors': [
            '.article-body', '.article-content',
            '.news-content', '.content-body',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 今周刊 (businesstoday.com.tw) ──
    'businesstoday': {
        'indicators': ['businesstoday.com.tw'],
        'selectors': [
            '.article-content', '.content-body',
            '.article-body', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 康健雜誌 (commonhealth.com.tw) ──
    'commonhealth': {
        'indicators': ['commonhealth.com.tw'],
        'selectors': [
            '.article-body', '.article-content',
            '.content-body', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 天下雜誌 (cw.com.tw) ──
    'cw': {
        'indicators': ['cw.com.tw'],
        'selectors': [
            '.article-body', '.content', '.article-content',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 商業週刊 / 親子天下 / cheers ──
    'businessweekly': {
        'indicators': ['businessweekly.com.tw'],
        'selectors': [
            '.article-body',
            '.article__content',
            '#article-body',
            '.article-content',
            '[class*="article-body"]',
            '.entry-content',
            'article',
        ]
    },
    'parenting': {
        'indicators': ['parenting.com.tw'],
        'selectors': [
            '.article-body',
            '.single-content',
            '.article-content',
            '.content-body',
            '[itemprop="articleBody"]',
            'article',
        ]
    },
    'cheers': {
        'indicators': ['cheers.com.tw'],
        'selectors': [
            '.article-body',
            '.article-content',
            '.content-detail',
            '[itemprop="articleBody"]',
            'article',
        ]
    },
    # ── 自由時報（ltn.com.tw，含 news/ec/m 等子域）──
    # 靜態 HTML 為 JS 渲染佔位，headless 執行後 .article_body 有完整全文
    # AMP 版（/amp/article/...）為靜態且有 .article_body
    'ltn': {
        'indicators': ['ltn.com.tw'],
        'selectors': [
            '.article_body', '#article_body',
            '.text', '.content940',
            '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 中央社 (cna.com.tw) ──
    # 靜態 HTML 即有完整內文，包在 article.article > .paragraph 裡
    'cna': {
        'indicators': ['cna.com.tw'],
        'selectors': [
            'article.article',
            '.centralContent',
            '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 鏡週刊 (mirrormedia.mg) ──
    # Next.js + styled-components（class 名稱含 hash，不穩定）
    # 優先嘗試 [class*="ArticleBody"]；fallback 走 _extract_from_json_ld（JSON-LD 有完整 articleBody）
    'mirrormedia': {
        'indicators': ['mirrormedia.mg'],
        'selectors': [
            '[class*="ArticleBody"]',
            '[class*="articleBody"]',
            '[class*="article-content"]',
            '[class*="story-body"]',
            'article', 'main',
        ]
    },
    # ── TechNews 科技新報 (technews.tw) ──
    # WordPress 架構，.entry-content 是標準選擇器
    'technews': {
        'indicators': ['technews.tw'],
        'selectors': [
            '.entry-content',
            '.articleContent_text',
            '.newsLetter_articleContent',
            '.article-content',
            'article',
        ]
    },
}

# ⭐️ [v3.8] 抽取前要移除的 CMP（Cookie 同意視窗）容器
#    避免 cookie 分類說明文字被誤判為主文；即使遮罩沒成功關閉也能擋住。
CMP_REMOVE_SELECTORS = [
    "#onetrust-consent-sdk", "#onetrust-banner-sdk", "#onetrust-pc-sdk",
    "#ot-sdk-container", "#ot-sdk-cookie-policy", ".onetrust-pc-dark-filter",
    "[id^='onetrust']", "[class*='onetrust']", "[class*='ot-sdk']",
    "[id*='fides']", "[class*='fides']",
    "[id*='cookie-consent']", "[class*='cookie-consent']",
]

HEURISTIC_CONF_THRESHOLD = 0.55


class UnsupportedSiteError(Exception):
    """爬取不支援的網站時拋出（如需登入、強反爬蟲）。
    呼叫端應視為 status='skipped'，不算爬取失敗。
    """
    pass


class HeadlessCrawler:
    def __init__(self, log_callback: Optional[Callable[[str], None]] = None,
                 use_proxy: bool = False):
        self.driver = None  # 型別為 uc.Chrome
        self.max_wait_time = 15
        self.scroll_pause_time = 1.5
        self.domain_selector_cache = {}
        self.genai_api_key = None
        self.log_callback = log_callback

        # ⭐ Tier 3：是否使用 Webshare 代理（預設 False；僅 use_proxy=True
        #    且環境變數有設定憑證時才生效，否則為 None = 不掛代理）。
        self.proxy_config = None
        if use_proxy:
            try:
                from tiered_fallback import load_proxy_config
                self.proxy_config = load_proxy_config()
            except Exception:
                self.proxy_config = None

        env_key = os.environ.get("GENAI_API_KEY")
        if env_key:
            self.configure_genai(env_key)

    def _log(self, message: str):
        print(message, flush=True)
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def configure_genai(self, api_key: str):
        """設定 Gemini API Key。
        對齊 Colab v3.8：新版 google-genai 不再使用全域 genai.configure，
        而是在呼叫時以 genai.Client(api_key=...) 建立 client，故此處僅保存金鑰。
        """
        if HAS_GENAI and api_key:
            self.genai_api_key = api_key
            self._log(f"[Crawler] Gemini configured with key: ...{api_key[-4:]}")

    def _init_driver(self):
        """初始化 undetected-chromedriver（統一作法，對齊 Colab v3.8）。"""
        chrome_bin = os.environ.get("CHROME_BIN")
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")

        if not chrome_bin:
            raise RuntimeError(
                "找不到 Chrome 執行檔；請確認已安裝 google-chrome 或設定環境變數 CHROME_BIN"
            )

        self._log("[INIT] 使用 undetected-chromedriver（統一作法，對齊 Colab）")
        options = uc.ChromeOptions()
        options.binary_location = chrome_bin
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=zh-TW")
        # ⭐️ --accept-lang 直接控制 Accept-Language HTTP header（從啟動即生效，
        #    套用到第一個導航主請求）。僅 --lang 只設 UI 語言，會讓網站（如維基百科）
        #    依 IP geo 導向非中文版本。
        options.add_argument("--accept-lang=zh-TW,zh;q=0.9")
        options.add_argument(f"--user-agent={DEFAULT_UA}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--dns-prefetch-disable")

        # 對齊 Colab v3.8：eager 策略（等 DOMContentLoaded，不等所有資源）。
        # Cloud Run 跨國載入較慢，但「不管時間、確保滾到底抓完整內文」優先。
        options.page_load_strategy = "eager"

        # ⭐ Tier 3：掛載 Webshare 代理（僅 self.proxy_config 有值時；預設不執行）
        if self.proxy_config:
            try:
                from tiered_fallback import apply_proxy_to_options
                apply_proxy_to_options(options, self.proxy_config, log_fn=self._log)
            except Exception as e:
                self._log(f"[Tier3] 代理掛載失敗（改用直連）：{e}")

        uc_params = {
            "options": options,
            "browser_executable_path": chrome_bin,
        }
        # 若已指定 chromedriver path（Docker 內預先安裝），強制使用以避免下載
        if chromedriver_path and os.path.exists(chromedriver_path):
            uc_params["driver_executable_path"] = chromedriver_path

        self.driver = uc.Chrome(**uc_params)

        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd(
                "Network.setExtraHTTPHeaders",
                {"headers": {"Accept-Language": ZH_ACCEPT_LANGUAGE}},
            )
            self.driver.execute_cdp_cmd(
                "Emulation.setTimezoneOverride",
                {"timezoneId": "Asia/Taipei"},
            )
        except Exception as e:
            self._log(f"[INIT] CDP 設定略過: {e}")

        # 不管時間優先：放寬頁面載入逾時，讓跨國重型站（eager DOMContentLoaded
        # 可達 90s+）能完整載入。逾時時 _open 仍會容忍（用已載入 DOM）。
        self.driver.set_page_load_timeout(120)
        self.driver.set_script_timeout(30)
        self._log("[INIT] ✓ undetected-chromedriver 已就緒（頁面120s，腳本30s）")
        return self.driver

    def _apply_locale_spoofing_js(self):
        js_patch = r"""
        try {
          Object.defineProperty(navigator, 'language', {get: () => 'zh-TW'});
          Object.defineProperty(navigator, 'languages', {get: () => ['zh-TW','zh','en']});
          try { Object.defineProperty(Intl.DateTimeFormat().resolvedOptions(), 'timeZone', {get: () => 'Asia/Taipei'}); } catch (e) {}
        } catch (e) {}
        """
        try:
            self.driver.execute_script(js_patch)
        except Exception:
            pass

    def _open(self, url: str, max_retries: int = 2):
        """打開網頁，對齊 Colab v3.8：eager 策略 driver.get + 重試。

        - 不在此處 window.stop()：保持網路開啟，讓後續 _scroll 階段能觸發
          lazy-load 載入所有區塊（GQ 等滾動式頁面的內容靠此載入）。
        - eager 載入逾時（>120s）時容忍：用已載入的 DOM 繼續，不判失敗。
        - 僅「真正的網路連線錯誤」才重試。
        """
        for attempt in range(1, max_retries + 1):
            try:
                self._log(f"[載入] 開啟網頁 (嘗試 {attempt}/{max_retries}): {url}")
                self.driver.get(url)
                self._apply_locale_spoofing_js()
                self._log("[載入] ✓ 網頁已載入（DOMContentLoaded）")
                return
            except TimeoutException:
                # eager 載入逾時：容忍，用已載入的 DOM；不重試（重試一樣慢）。
                self._log("[載入] ⚠️ 載入逾時，使用已載入內容繼續（網路保持，供後續滾動 lazy-load）")
                try:
                    self._apply_locale_spoofing_js()
                except Exception:
                    pass
                return
            except Exception as e:
                error_msg = str(e).lower()
                net_err = any(kw in error_msg for kw in [
                    'connection aborted', 'connection refused', 'connection reset',
                    'err_connection', 'err_network', 'err_name_not_resolved',
                    'unreachable', 'dns'
                ])
                if net_err and attempt < max_retries:
                    self._log(f"[載入] ⚠️ 網路連線錯誤 (嘗試 {attempt}/{max_retries})，重試: {e}")
                    try:
                        self.driver.execute_script("window.stop();")
                    except Exception:
                        pass
                    time.sleep(1)
                    continue
                elif net_err:
                    raise TimeoutError(f"網路連線錯誤，已重試 {max_retries} 次: {url}")
                else:
                    raise

    def _apply_meta_fallback(self, content: str, html: str) -> str:
        """主文過短（< 200 字）時，補入 og:description / meta description 作為導語。
        對齊 Colab v3.8 _extract_main_text 末段邏輯。
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')
            meta_desc = None
            # 優先 og:description
            ogd = soup.find('meta', attrs={'property': 'og:description'})
            if ogd and ogd.get('content'):
                meta_desc = ogd['content'].strip()
            # 次選 name=description
            if not meta_desc:
                m = soup.find('meta', attrs={'name': 'description'})
                if m and m.get('content'):
                    meta_desc = m['content'].strip()
            if meta_desc and meta_desc not in content:
                self._log(f"[Fallback] 主文過短（{len(content)} 字），補入 meta description")
                return meta_desc + "\n\n" + content
        except Exception:
            pass
        return content

    def _extract_from_json_ld(self, html: str) -> str:
        """從 JSON-LD <script> 中萃取 articleBody 文字。

        適用於 MirrorMedia 等 Next.js 站台：內文以 JSON-LD NewsArticle schema 嵌入，
        headless 瀏覽器渲染後 DOM 仍可能難以用 CSS selector 抓到（styled-components hash），
        但 JSON-LD 在初始 HTML 中就完整存在。
        """
        try:
            # 找所有 application/ld+json script
            ld_scripts = re.findall(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                html, re.DOTALL | re.I
            )
            for raw_json in ld_scripts:
                try:
                    data = json.loads(raw_json.strip())
                except Exception:
                    continue
                # 支援 @graph 陣列
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get('@graph'):
                        items = item['@graph']
                        break
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    body = item.get('articleBody', '')
                    if body and len(body) >= 200:
                        self._log(f"[JSON-LD] 從 @type={item.get('@type', '?')} 抽到 {len(body)} 字")
                        return self._clean_text(body)
        except Exception as e:
            self._log(f"[JSON-LD] 萃取失敗: {e}")
        return ""

    def _extract_from_block_payload(self, html: str) -> str:
        """從現代框架（Next.js App Router / RSC、Condé Nast Copilot 等）的
        序列化 block payload 抽取主文。

        支援兩種格式：
        1. 簡化格式：["p","段落文字"]、["blockquote","引言"]
        2. React RSC 格式：["$","p","key",{"children":"文字"}]
                           ["$","p",null,{"className":"...","children":["文字段落"]}]
        """
        try:
            seen = set()
            parts = []

            def _add(text):
                text = (text or "").strip()
                if len(text) >= 10 and text not in seen:
                    seen.add(text)
                    parts.append(text)

            # 格式1：["p","..."] / ["blockquote","..."] / ["h1~h6","..."]
            # re.DOTALL 讓 [^"\\] 能匹配包含換行的段落（修正多行段落漏抓）
            pat1 = re.compile(r'\["(p|blockquote|h[1-6])","((?:[^"\\]|\\.|\n)*)"\]', re.DOTALL)
            for m in pat1.finditer(html):
                raw = m.group(2)
                try:
                    text = json.loads('"' + raw + '"')
                except Exception:
                    text = raw
                _add(text)

            # 格式2：["$","p","key",{"children":"..."}] (React RSC)
            # children 可為字串或陣列，re.DOTALL 同上修正多行漏抓
            pat2 = re.compile(
                r'\["\$","(?:p|blockquote|h[1-6])",[^,]*,\{"[^}]*"children":"((?:[^"\\]|\\.|\n)*)"\}',
                re.DOTALL
            )
            for m in pat2.finditer(html):
                raw = m.group(1)
                try:
                    text = json.loads('"' + raw + '"')
                except Exception:
                    text = raw
                _add(text)

            # 格式3：純文字字串（至少15字，在 RSC JSON 串流中）
            # 比對 script 區段中較長的中文/英文字串段落
            pat3 = re.compile(r'"([一-鿿㐀-䶿][^\\"]{14,})"')
            for m in pat3.finditer(html):
                raw = m.group(1)
                try:
                    text = json.loads('"' + raw + '"')
                except Exception:
                    text = raw
                _add(text)

            return self._clean_text("\n".join(parts))
        except Exception as e:
            self._log(f"[Block Payload] 抽取失敗: {e}")
            return ""

    def _clear_overlays_and_click_cta(self, rounds: int = 3):
        """遮罩處理：對齊 Colab v3.8。
        順序：OneTrust（AllowAll JS → 按鈕）→ Fides（JS API）→ 通用點擊後備。
        """
        # ========= ⭐️ [v3.8] 優先偵測並處理 OneTrust CMP（Vogue Taiwan 等）=========
        try:
            has_onetrust = self.driver.execute_script(
                "return !!(window.OneTrust || document.getElementById('onetrust-banner-sdk') "
                "|| document.getElementById('onetrust-consent-sdk'));"
            )
        except Exception:
            has_onetrust = False

        if has_onetrust:
            self._log("[遮罩處理] 偵測到 OneTrust CMP，嘗試「全部接受」...")
            ot_done = False
            # 方法 A: 直接呼叫 OneTrust JS API（最穩健，繞過 DOM 點擊）
            try:
                ot_result = self.driver.execute_script("""
                    try {
                        if (window.OneTrust && typeof window.OneTrust.AllowAll === 'function') {
                            window.OneTrust.AllowAll();
                            if (typeof window.OneTrust.Close === 'function') { window.OneTrust.Close(); }
                            return 'AllowAll';
                        }
                        return 'no-api';
                    } catch (e) { return 'err:' + e.message; }
                """)
                if ot_result == 'AllowAll':
                    ot_done = True
                    self._log("[遮罩處理] ✓ OneTrust.AllowAll() 已呼叫")
                else:
                    self._log(f"[遮罩處理] OneTrust JS API 無法使用 ({ot_result})，改用按鈕點擊...")
            except Exception as e:
                self._log(f"[遮罩處理] OneTrust JS API 失敗：{e}")

            # 方法 B: 點擊「全部接受」按鈕
            if not ot_done:
                try:
                    btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
                    )
                    self.driver.execute_script("arguments[0].click();", btn)
                    ot_done = True
                    self._log("[遮罩處理] ✓ 已點擊 OneTrust「全部接受」按鈕")
                except TimeoutException:
                    self._log("[遮罩處理] 找不到 OneTrust 接受按鈕，改用通用邏輯...")

            # 等待 OneTrust 橫幅與暗幕消失
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.invisibility_of_element_located(
                        (By.CSS_SELECTOR, "#onetrust-banner-sdk, .onetrust-pc-dark-filter")
                    )
                )
            except TimeoutException:
                pass

            # ⭐️ 偵測到 OneTrust 即視為已處理並 return：成功關閉最好；
            #    即使未完全關閉，CMP 容器也會在抽取前由 _remove_cmp_containers 移除。
            #    不再往下跑 Fides 偵測（10s）與通用後備（多輪 sleep），避免浪費時間
            #    導致大頁面超時（OneTrust 與 Fides 互斥，有 OneTrust 就不會有 Fides）。
            self._log(f"[遮罩處理] OneTrust 處理結束（ot_done={ot_done}），跳過後續後備。")
            return

        # ========= ⭐️ [v3.6+] 處理 Fides (GQ/Vogue) - JS API 方案 =========
        try:
            # ⭐️ 先快速偵測 Fides 是否存在，沒有就立即跳過（避免對無 CMP 網站
            #    （如維基百科）空等 10 秒，累積導致 60s 硬限超時）。
            if not self.driver.execute_script("return !!window.Fides"):
                raise NoSuchElementException("頁面無 Fides CMP，跳過 Fides 處理")
            self._log("[遮罩處理] 偵測到 Fides，等待 consent 載入...")
            WebDriverWait(self.driver, 5).until(
                lambda d: d.execute_script("return !!window.Fides && typeof window.Fides.consent === 'object'"),
                "Fides consent 未能在 5 秒內載入"
            )
            self._log("[遮罩處理] ✓ Fides API 已載入。正在讀取 consent keys...")

            consent_prefs = self.driver.execute_script("return window.Fides.consent")
            if not consent_prefs or not isinstance(consent_prefs, dict):
                raise Exception(f"Failed to read window.Fides.consent object. Got: {consent_prefs}")

            all_true_prefs = {key: True for key in consent_prefs.keys()}
            self._log(f"[遮罩處理] 正在準備 '全部同意' payload... (keys: {list(all_true_prefs.keys())})")

            script = """
            const prefs = arguments[0];
            if (!window.Fides) return 'Fides object missing';
            try {
                if (typeof window.Fides.updateConsent === 'function') {
                    window.Fides.updateConsent(prefs);
                    return 'Called Fides.updateConsent()';
                } else if (typeof window.Fides.savePreferences === 'function') {
                    window.Fides.savePreferences(prefs);
                    return 'Called Fides.savePreferences()';
                } else {
                    window.Fides.consent = prefs;
                    if (typeof window.Fides.showModal === 'function') {
                         window.Fides.showModal(false);
                    }
                    return 'Fallback: Set Fides.consent and hid modal';
                }
            } catch (e) {
                return e.message;
            }
            """
            result = self.driver.execute_script(script, all_true_prefs)
            self._log(f"[遮罩處理] ✓ Fides API 呼叫完畢。 ({result})")

            try:
                WebDriverWait(self.driver, 5).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, "iframe[id='fides-iframe'], iframe[title*='Fides']"))
                )
            except TimeoutException:
                self._log("[遮罩處理] ...iframe 未消失, 檢查 #fides-overlay...")
                try:
                    WebDriverWait(self.driver, 3).until(
                        EC.invisibility_of_element_located((By.CSS_SELECTOR, "#fides-overlay"))
                    )
                except TimeoutException:
                    pass

            self._log("[遮罩處理] ✓ Fides 遮罩已確認消失。")
            return

        except (NoSuchElementException, TimeoutException) as e:
            self._log(f"[遮罩處理] Fides API 處理失敗 ({e})，回退至通用邏輯...")
        except Exception as e:
            self._log(f"[遮罩處理] Fides API 處理時發生非預期錯誤: {e}")

        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

        # ========= 通用後備邏輯 =========
        self._log("[遮罩處理] ...執行通用後備邏輯...")

        def click_candidates():
            btn_texts = "|".join(MASK_BUTTON_PATTERNS)
            script = f"""
            const patterns = [/{btn_texts}/i];
            function textOf(el) {{
              let t = (el.innerText || el.textContent || '').trim();
              if (!t && el.getAttribute) t = (el.getAttribute('aria-label') || '').trim();
              return t;
            }}
            const nodes = Array.from(document.querySelectorAll('button, a, [role=button], [aria-label]'));
            for (const el of nodes) {{
              const t = textOf(el);
              if (!t) continue;
              for (const p of patterns) {{
                if (p.test(t)) {{
                  try {{ el.scrollIntoView({{block:'center'}}); }} catch(e) {{}}
                  try {{ el.click(); return true; }} catch(e) {{}}
                  try {{ el.dispatchEvent(new MouseEvent('click', {{bubbles:true}})); return true; }} catch(e) {{}}
                }}
              }}
            }}
            return false;
            """
            try:
                return bool(self.driver.execute_script(script))
            except Exception:
                return False

        for _ in range(rounds):
            if click_candidates():
                time.sleep(0.5)
            for sel in OVERLAY_SELECTORS:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in elements:
                        try:
                            self.driver.execute_script("arguments[0].style.display='none'", el)
                        except Exception:
                            pass
                except Exception:
                    continue
            time.sleep(0.5)

    def _is_listing_page(self, soup: BeautifulSoup) -> bool:
        self._log("[Page Type Analysis] Starting analysis...")
        articles = soup.find_all('article', limit=10)
        if len(articles) >= 5:
            self._log(f"[Page Type Analysis] Judgement: LISTING PAGE (found {len(articles)} <article> tags).")
            return True
        if 2 <= len(articles) < 5:
            # Article pages often have 1 main + 2-3 related-article cards.
            # Only call it a listing if the articles are similarly sized (none dominates).
            text_lens = sorted([len(a.get_text(strip=True)) for a in articles], reverse=True)
            avg_rest = sum(text_lens[1:]) / max(len(text_lens) - 1, 1)
            if text_lens[0] > max(3 * avg_rest, 500):
                self._log(f"[Page Type Analysis] {len(articles)} <article> tags but largest ({text_lens[0]} chars) dominates — SINGLE ARTICLE PAGE.")
            else:
                self._log(f"[Page Type Analysis] Judgement: LISTING PAGE ({len(articles)} similarly-sized <article> tags).")
                return True
        list_items = soup.find_all('li', limit=20)
        if len(list_items) > 5:
            article_like_li = 0
            for item in list_items:
                if item.find('a') and len(item.get_text(strip=True)) > 20:
                    article_like_li += 1
            if article_like_li > 5:
                self._log(f"[Page Type Analysis] Judgement: LISTING PAGE (found {article_like_li} article-like <li> items).")
                return True
        self._log("[Page Type Analysis] Judgement: SINGLE ARTICLE PAGE.")
        return False

    def _scroll_and_wait_for_full_load(self, max_scrolls: int = 60, original_url: str = None):
        """滾動到底以觸發 lazy-load（對齊 Colab v3.8 _scroll_page_safe）。

        逐次滾到底、等待，直到頁面高度連續穩定（stagnant）= 已到底。
        max_scrolls 上限防 infinity scroll（高度無限成長的 feed 頁）造成無限迴圈。
        網路在此階段保持開啟（_open 不再提早 window.stop），lazy-load 才能載入。
        若 original_url 已提供，每次捲動後偵測 URL 是否因無限捲動換頁（pushState）
        而改變，一旦改變立即停止並回傳 True，主流程應改用換頁前的 DOM 快照。

        Returns:
            True 若發生 URL 換頁（主流程應用 pre-scroll DOM 快照），否則 False。
        """
        self._log("[Scroll & Wait] Starting full page scroll for single article...")
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        stagnant_scrolls = 0
        max_stagnant_scrolls = 3
        scrolls = 0
        url_changed = False
        while stagnant_scrolls < max_stagnant_scrolls and scrolls < max_scrolls:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(self.scroll_pause_time)
            scrolls += 1
            # 偵測無限捲動換頁（A Day Magazine / ELLE 等現代媒體的 pushState 換頁）
            if original_url:
                try:
                    current = self.driver.current_url
                    if current != original_url:
                        self._log(f"[Scroll & Wait] URL 已換頁（{current}），停止捲動以避免抓到下一篇")
                        url_changed = True
                        break
                except Exception:
                    pass
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height > last_height:
                self._log(f"[Scroll & Wait] Height → {new_height}px (scroll {scrolls}/{max_scrolls})")
                last_height = new_height
                stagnant_scrolls = 0
            else:
                stagnant_scrolls += 1
                self._log(f"[Scroll & Wait] Height stable {stagnant_scrolls}/{max_stagnant_scrolls} (scroll {scrolls})")
        if scrolls >= max_scrolls:
            self._log(f"[Scroll & Wait] 達滾動上限 {max_scrolls}（疑似 infinity scroll），停止。")
        elif not url_changed:
            self._log("[Scroll & Wait] 頁面高度已穩定，視為載入完整。")
        # 滾到底後回到頂部並緩衝，確保所有 lazy 區塊都已渲染進 DOM
        if not url_changed:
            try:
                self.driver.execute_script("window.scrollTo(0, 0);")
            except Exception:
                pass
            time.sleep(2)
        return url_changed

    def _wait_for_marieclaire_content(self):
        """特別為 marieclaire 等待文章內容載入"""
        self._log("[Marie Claire] Waiting for article content to load...")
        selectors_to_wait = [
            '.articleContent',
            '[id*="content"]',
            '.articleContainer',
            'article p'
        ]
        for sel in selectors_to_wait:
            try:
                self._log(f"[Marie Claire] Checking for: {sel}")
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
                self._log(f"[Marie Claire] ✅ Found: {sel}")
                time.sleep(2)
                return True
            except TimeoutException:
                self._log(f"[Marie Claire] ❌ Not found: {sel}")
                continue
        self._log("[Marie Claire] ⚠️ No article content selectors matched, proceeding anyway")
        return False

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        # 中文字 4 字以上即可成行（一個短句如「不知道。」4 字有意義）
        lines = [line.strip() for line in text.splitlines() if line.strip() and len(line.strip()) >= 4]
        return "\n\n".join(lines)

    # 台灣新聞站常見「尾部樣板」：贊助 CTA、APP 下載、版權宣告、社群分享。
    # 文章正文之後才會出現，故只在「累積足夠正文後」遇到才裁切（保守，不誤傷短文）。
    _TRAILING_BOILERPLATE = (
        # 中央社
        "支持中央社", "下載中央社", "一手新聞", "本網站之文字", "非經授權",
        "小額贊助", "選擇與事實站在一起", "守護新聞自由",
        # 自由時報
        "一手掌握", "點我訂閱", "點我下載", "不用抽", "你可能有興趣",
        "今日熱門新聞", "注目新聞", "Recommended by",
        # 鏡週刊
        "支持鏡週刊", "加入訂閱會員", "贊助本文",
        # 科技新報 / TechNews
        "請我們喝杯咖啡", "訂閱免費電子報", "您也可能喜歡", "科技新報粉絲團",
        "從這裡可透過", "科技新知，時時更新",
        # 通用版權/訂閱/分享（皆為文末不可能出現在正文中段的明確樣板）
        "不得轉載", "版權所有", "著作權所有", "未經授權", "禁止轉載",
        "點我加入", "訂閱電子報", "立即下載", "下載APP", "下載 APP",
        "更多內容請見", "授權轉載",
    )

    def _trim_trailing_boilerplate(self, content: str, min_keep: int = 150) -> str:
        """裁掉文章正文之後的尾部樣板（贊助／APP／版權等）。

        只在累積正文已達 min_keep 字後，遇到樣板行才截斷；之前的不動，
        避免短文或正文中偶然含關鍵字時被誤砍。
        """
        if not content:
            return content
        lines = content.split("\n")
        kept = []
        acc = 0
        for line in lines:
            ls = line.strip()
            if acc >= min_keep and any(bp in ls for bp in self._TRAILING_BOILERPLATE):
                self._log(f"[Trim] 尾部樣板截斷於：{ls[:30]}")
                break
            kept.append(line)
            acc += len(ls)
        return "\n".join(kept).strip()

    # 瀏覽器錯誤頁 / 反爬蟲挑戰頁（Cloudflare 等）的特徵字串。命中代表抓到的不是真正內容
    # （站台連不上，或被反爬蟲攔下顯示驗證頁），應視為失敗，讓分層 fallback（Tier 3 代理）接手。
    _BROWSER_ERROR_MARKERS = (
        # Chrome 連線錯誤頁
        "This site can’t be reached", "This site can't be reached",
        "refused to connect", "took too long to respond",
        "ERR_CONNECTION", "ERR_NAME_NOT_RESOLVED", "ERR_TIMED_OUT",
        "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT", "ERR_ADDRESS_UNREACHABLE",
        "DNS_PROBE_FINISHED", "ERR_SSL", "ERR_CERT", "ERR_EMPTY_RESPONSE",
        "無法連上這個網站", "拒絕連線", "回應時間過長", "找不到該網頁的位址",
        "no proxy", "Checking the proxy",
        # Cloudflare / 反爬蟲挑戰頁（Dcard 等）
        "需要確認您的連線是安全的", "Enable JavaScript and cookies to continue",
        "Checking your connection", "Verifying you are human",
        "Just a moment", "DDoS protection by Cloudflare", "cf-browser-verification",
        "請稍候，並依據指示", "Verify you are human", "Performance & security by Cloudflare",
    )

    def _looks_like_browser_error_page(self, content: str, title: str = "") -> bool:
        """判斷抽取到的內容是否為瀏覽器連線錯誤頁（而非真正文章）。

        條件（保守，避免誤判真文章）：內容偏短（< 1500 字）且命中錯誤特徵字串。
        錯誤頁通常很短且 title 僅為網域名稱。
        """
        if not content:
            return False
        if len(content) >= 1500:
            return False  # 長內容幾乎不可能是錯誤頁
        hits = sum(1 for m in self._BROWSER_ERROR_MARKERS if m in content)
        # 命中 1 個強特徵即可（這些字串幾乎不會出現在正常文章正文）
        return hits >= 1

    def _css_path(self, el) -> str:
        try:
            parts = []
            cur = el
            while cur and getattr(cur, 'name', None) and cur.name != 'html':
                try:
                    ident = cur.name
                    el_id = cur.get('id') if hasattr(cur, 'get') else None
                    if el_id:
                        ident += f"#{el_id}"
                    classes = cur.get('class') if hasattr(cur, 'get') else None
                    if classes and isinstance(classes, (list, tuple)):
                        ident += '.' + '.'.join(str(c) for c in classes[:3])
                    parts.append(ident)
                    cur = cur.parent if hasattr(cur, 'parent') else None
                except Exception:
                    break
                if len(parts) > 20:
                    break
            return ' > '.join(reversed(parts))
        except Exception:
            return "unknown"

    def _get_element_depth(self, el) -> int:
        try:
            depth = 0
            current = el
            while current and hasattr(current, 'parent'):
                depth += 1
                current = current.parent
                if depth > 50:
                    break
            return depth
        except Exception:
            return 0

    def _build_dom_summary(self, soup: BeautifulSoup, max_count: int = 150) -> List[Dict[str, Any]]:
        cands = []
        for node in soup.find_all(['article', 'section', 'div', 'main']):
            try:
                if not node or not hasattr(node, 'get_text') or not hasattr(node, 'find_all'):
                    continue
                text = node.get_text(' ', strip=True)
                pcount = len(node.find_all('p'))
                if pcount >= 3 or len(text) > 300:
                    links_text = ''.join(a.get_text(strip=True) for a in node.find_all('a'))
                    ld = len(links_text) / max(len(text), 1)
                    cands.append({
                        'css_path': self._css_path(node)[:512],
                        'text_len': len(text),
                        'p_count': pcount,
                        'link_density': round(ld, 3),
                        'preview': text[:120]
                    })
            except Exception:
                continue
        cands.sort(key=lambda x: x['text_len'], reverse=True)
        return cands[:max_count]

    def _ask_gemini_selector(self, url: str, soup: BeautifulSoup) -> List[str]:
        """向 Gemini 詢問主文容器選擇器（回傳多組建議）。
        對齊 Colab v3.8：改用新的 google-genai 套件（genai.Client + models.generate_content），
        優先 gemini-2.5-flash，失敗回退 gemini-2.5-flash-lite，temperature=0.3。
        """
        if not HAS_GENAI or not self.genai_api_key:
            return []
        self._log(f"[LLM] Asking Gemini for selector: {url}")
        text = ""
        try:
            dom_summary = self._build_dom_summary(soup)
            prompt_sys = (
                "你是資深前端工程師。根據下列 DOM 摘要，判斷最可能代表文章主文的容器，"
                "只回傳 JSON：{\"selector\":\"...\",\"confidence\":0~1,\"alternatives\":[\"...\",\"...\"]}。"
                "選擇器請用 CSS（可含 class/id/層級），避免脆弱的 :nth-child。不要回原文、不要多餘說明。"
            )
            user_payload = {"url": url, "candidates": dom_summary}
            prompt_text = prompt_sys + "\n\n" + json.dumps(user_payload, ensure_ascii=False)[:30000]

            client = genai.Client(api_key=self.genai_api_key)
            try:
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt_text,
                    config=types.GenerateContentConfig(temperature=0.3),
                )
            except Exception:
                self._log(f"[LLM] Gemini 2.0 Flash 不可用，改用 1.5 Flash - {url}")
                resp = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=prompt_text,
                    config=types.GenerateContentConfig(temperature=0.3),
                )

            text = (getattr(resp, "text", None) or "").strip()
            # 穩健去除 markdown fence，再抽取 {...}（對齊 C2 修正）
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
            text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
            text = text.strip()
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                text = json_match.group(0)
            data = json.loads(text)

            main_sel = data.get("selector")
            alt_list = data.get("alternatives") or []
            selectors = []
            if isinstance(main_sel, str) and main_sel.strip():
                selectors.append(main_sel.strip())
            if isinstance(alt_list, list):
                for s in alt_list:
                    if isinstance(s, str) and s.strip():
                        selectors.append(s.strip())
            dedup = []
            for s in selectors:
                if s not in dedup:
                    dedup.append(s)
            self._log(f"[LLM] Suggested selectors: {dedup}")
            return dedup
        except json.JSONDecodeError as e:
            self._log(f"[LLM] JSON 解析失敗：{e}, 原文：{text[:200]} - {url}")
            return []
        except Exception as e:
            self._log(f"[LLM] Gemini call failed: {e}")
            return []

    def _calculate_visual_weight(self, node, soup: BeautifulSoup) -> float:
        try:
            if node is None or not hasattr(node, 'get'):
                return 1.0
            classes = node.get('class')
            classes_str = ' '.join(str(c) for c in classes).lower() if classes else ''
            id_attr = node.get('id')
            id_str = str(id_attr).lower() if id_attr else ''
            weight = 1.0
            if any(x in classes_str or x in id_str for x in ['main', 'content', 'center', 'article']):
                weight += 0.3
            if any(x in classes_str or x in id_str for x in ['side', 'sidebar', 'widget', 'aside']):
                weight -= 0.5
            if any(x in classes_str or x in id_str for x in ['header', 'footer', 'nav']):
                weight -= 0.4
            return max(0.1, weight)
        except Exception:
            return 1.0

    def _calculate_dom_depth(self, node) -> int:
        try:
            if not node:
                return 0
            depth = 0
            current = node
            while current and hasattr(current, 'parent'):
                depth += 1
                current = current.parent
                if depth > 50:
                    break
            return depth
        except Exception:
            return 5

    def _calculate_paragraph_quality(self, node) -> float:
        try:
            if not node or not hasattr(node, 'find_all'):
                return 0.0
            paragraphs = node.find_all('p')
            if not paragraphs:
                return 0.0
            total_score = 0.0
            for p in paragraphs:
                try:
                    text = p.get_text(strip=True)
                    if len(text) < 20:
                        continue
                    punctuation_count = sum(1 for c in text if c in '。，！？；：、')
                    punct_density = punctuation_count / max(len(text), 1)
                    if 0.03 <= punct_density <= 0.15:
                        total_score += 1.0
                    else:
                        total_score += 0.5
                except Exception:
                    continue
            return total_score / max(len(paragraphs), 1)
        except Exception:
            return 0.0

    def _calculate_chinese_ratio(self, text: str) -> float:
        if not text:
            return 0.0
        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        return chinese_chars / max(len(text), 1)

    def _looks_like_listing_block(self, node) -> bool:
        if len(node.find_all('article', recursive=False)) > 3 or len(node.find_all('li', recursive=False)) > 5:
            self._log(f"[Filter] Node disqualified by structure (contains multiple <article> or <li>).")
            return True
        text = node.get_text(" ", strip=True).lower()
        if not text:
            return False
        listing_keywords = [
            '延伸閱讀', '相關文章', '推薦閱讀', '熱門文章', 'entertainment', '美麗佳人編輯部',
            'articlelist', 'storylist', 'postlist', 'item-list', 'card-list'
        ]
        matched_keywords = [kw for kw in listing_keywords if kw in text]
        if matched_keywords:
            self._log(f"[Filter] Node disqualified by keywords: {matched_keywords}")
            return True
        return False

    def _looks_like_cookie_banner(self, text: str, node=None) -> bool:
        if not text:
            return False
        t = text.lower()
        cookie_keywords = [
            "cookie", "cookies", "gdpr", "consent", "同意管理", "隱私權", "隱私政策",
            "personal data", "個人資料", "adchoices", "targeted advertising",
            "performance cookies", "functional cookies", "audience measurement",
            "本網站使用", "這些 cookie", "這些 cookies"
        ]
        if sum(1 for kw in cookie_keywords if kw in t) >= 3:
            return True
        return False

    def _calculate_node_score(self, node, soup: BeautifulSoup) -> Tuple[float, Dict[str, float]]:
        scores = {}
        try:
            if not node or not hasattr(node, 'get_text'):
                return 0.0, {}
            text = node.get_text("\n", strip=True)
            text_len = len(text)
            if text_len < 100:
                return 0.0, {}
            if self._looks_like_cookie_banner(text, node) or self._looks_like_listing_block(node):
                return 0.0, {}
            scores['text_length'] = text_len * 0.2
            scores['paragraph_quality'] = self._calculate_paragraph_quality(node) * 1000 * 0.25
            links = node.find_all('a')
            link_text = ''.join(a.get_text(strip=True) for a in links)
            link_density = len(link_text) / max(text_len, 1)
            scores['link_density'] = (1 - link_density) * 500 * 0.25
            depth = self._calculate_dom_depth(node)
            optimal_depth = 8
            depth_score = 1.0 - abs(depth - optimal_depth) / max(depth, optimal_depth)
            scores['dom_depth'] = depth_score * 300 * 0.10
            scores['visual_weight'] = self._calculate_visual_weight(node, soup) * 400 * 0.10
            scores['chinese_ratio'] = self._calculate_chinese_ratio(text) * 300 * 0.10
            total_score = sum(scores.values())
            return total_score, scores
        except Exception:
            return 0.0, {}

    def _calculate_confidence(self, best_score: float, second_score: float, best_node: Any) -> float:
        margin_conf = min(1.0, max(best_score - second_score, 0.0) / best_score * 2) if best_score > 0 else 0.0
        if best_score >= 1500:
            score_conf = 1.0
        elif best_score >= 800:
            score_conf = 0.7 + (best_score - 800) / 700 * 0.3
        else:
            score_conf = best_score / 800 * 0.7
        structure_conf = 0.5
        try:
            if best_node.find(['h1', 'h2', 'h3']):
                structure_conf += 0.2
            if best_node.find(['time', '[datetime]']):
                structure_conf += 0.15
            if len(best_node.find_all('p')) >= 5:
                structure_conf += 0.15
        except Exception:
            pass
        structure_conf = min(1.0, structure_conf)
        final_conf = (margin_conf * 0.4 + score_conf * 0.3 + structure_conf * 0.3)
        return final_conf

    def _wait_for_content_load(self):
        try:
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except TimeoutException:
            self._log("[Crawler] Timeout waiting for body")
            return
        # 對齊 Colab：額外等待主內容選擇器（article / main / #content / .content）
        # 讓 JS 有機會渲染完成再抽取 DOM
        content_selectors = [
            (By.TAG_NAME, "article"),
            (By.TAG_NAME, "main"),
            (By.CSS_SELECTOR, "#content"),
            (By.CSS_SELECTOR, ".content"),
        ]
        for by, selector in content_selectors:
            try:
                WebDriverWait(self.driver, 5).until(EC.presence_of_element_located((by, selector)))
                self._log(f"[Crawler] Content element found: {selector}")
                break
            except TimeoutException:
                continue

    def _remove_cmp_containers(self, soup: BeautifulSoup):
        """⭐️ [v3.8] 抽取前移除 OneTrust / Fides / 通用 CMP 同意視窗容器。
        避免 cookie 分類說明文字被誤判為主文（即使遮罩沒成功關閉也能擋住）。
        """
        # 移除 Fides 遮罩留下的殘餘 append 容器
        try:
            fides_remnant = soup.find(id="fides-iframe-append")
            if fides_remnant:
                fides_remnant.decompose()
        except Exception:
            pass

        removed = 0
        for _sel in CMP_REMOVE_SELECTORS:
            try:
                for _el in soup.select(_sel):
                    _el.decompose()
                    removed += 1
            except Exception:
                continue
        if removed:
            self._log(f"  → [CMP] Removed {removed} cookie-consent container(s) before scoring")

    def _extract_main_text(self, html: str, url: str) -> str:
        self._log("=" * 80)
        self._log("[DEBUG MODE] Content Extraction Process Started")
        self._log("=" * 80)

        # Phase 0: 解析 HTML
        self._log("\n[Phase 0] HTML Parsing")
        soup = BeautifulSoup(html, 'html.parser')
        original_html_len = len(html)
        total_elements = len(soup.find_all())
        self._log(f"  → Original HTML length: {original_html_len:,} chars")
        self._log(f"  → Total DOM elements: {total_elements:,}")

        # Phase 1.1: 移除基本標籤
        self._log("\n[Phase 1.1] Removing basic tags (script, style, nav, footer, etc.)")
        removed_tags = []
        for tag_name in ['script', 'style', 'nav', 'footer', 'iframe', 'header', 'aside']:
            tags = soup.find_all(tag_name)
            if tags:
                removed_tags.append(f"{tag_name}({len(tags)})")
                for tag in tags:
                    tag.decompose()
        self._log(f"  → Removed tags: {', '.join(removed_tags) if removed_tags else 'None'}")

        # ⭐️ [v3.8] Phase 1.1b: 抽取前移除 CMP（cookie 同意）容器
        self._log("\n[Phase 1.1b] Removing CMP / cookie-consent containers (OneTrust / Fides)")
        self._remove_cmp_containers(soup)

        # Phase 2: 檢查緩存
        domain = urlparse(url).netloc
        if domain in self.domain_selector_cache:
            sel = self.domain_selector_cache[domain]
            self._log(f"\n[Phase 2] Cache Hit! Using cached selector for domain '{domain}'")
            self._log(f"  → Selector: '{sel}'")
            node = soup.select_one(sel)
            if node:
                content = self._clean_text(node.get_text("\n", strip=True))
                self._log(f"  → ✅ Content extracted: {len(content)} chars")
                self._log(f"  → Preview: {content[:200]}...")
                return content
            else:
                self._log(f"  → ⚠️ Cached selector no longer matches, clearing cache")
                del self.domain_selector_cache[domain]

        # Phase 2.0: 優先嘗試模板選擇器（在噪音過濾之前！）
        self._log("\n[Phase 2.0] Checking for Known Site Templates (BEFORE noise filtering)")
        template_matched = None
        template_elements_to_protect = set()

        # ⭐ 比對所有命中模板，選「最具體」者：
        #    網域型 indicator（含 '.'，如 cna.com.tw）比通用關鍵字（news/article/story）更具體，
        #    避免 'news' 通用模板搶先命中 cna.com.tw/news/... 而蓋掉專屬 cna 模板。
        url_lower = url.lower()
        matched_templates = []
        for tmpl_name, tmpl in SITE_TEMPLATES.items():
            best_ind = None
            for ind in tmpl['indicators']:
                if ind in url_lower:
                    if best_ind is None or len(ind) > len(best_ind):
                        best_ind = ind
            if best_ind is not None:
                # 具體度：含 '.' 的網域型 indicator +1000 權重，再加長度
                specificity = (1000 if '.' in best_ind else 0) + len(best_ind)
                matched_templates.append((specificity, tmpl_name, tmpl))
        matched_templates.sort(key=lambda x: x[0], reverse=True)

        if matched_templates:
            for _spec, tmpl_name, tmpl in matched_templates[:1]:
                template_matched = tmpl_name
                self._log(f"  → ✅ Matched template: '{tmpl_name}' (specificity={_spec})")
                if len(matched_templates) > 1:
                    others = ', '.join(t[1] for t in matched_templates[1:])
                    self._log(f"  → （其他命中但較不具體，略過：{others}）")
                self._log(f"  → Selectors to try: {tmpl['selectors']}")

                for sel in tmpl['selectors']:
                    try:
                        self._log(f"\n  [Trying selector: '{sel}']")
                        node = soup.select_one(sel)
                        if not node:
                            self._log(f"    → ❌ Selector did not match any element")
                            continue

                        template_elements_to_protect.add(id(node))

                        text = node.get_text("\n", strip=True)
                        self._log(f"    → ✅ Element found! Raw text length: {len(text)} chars, <p>: {len(node.find_all('p'))}")

                        cleaned = self._clean_text(text)
                        self._log(f"    → Cleaned text length: {len(cleaned)} chars")

                        if len(cleaned) >= 300:
                            self._log(f"    → ✅ SUCCESS! Content sufficient (>= 300 chars), caching selector")
                            if domain:
                                self.domain_selector_cache[domain] = sel
                            return cleaned
                        else:
                            self._log(f"    → ⚠️ Content too short (< 300 chars), trying next selector")
                    except Exception as e:
                        self._log(f"    → ❌ Selector failed with error: {e}")
                        continue

                self._log(f"\n  → ⚠️ Template selectors did not return sufficient content, falling back")
                break

        if not template_matched:
            self._log(f"  → No matching template found for this URL")

        # Phase 1.2: 噪音過濾（僅在模板失敗時執行）
        self._log("\n[Phase 1.2] Noise Filtering (ads, recommendations, related articles)")
        noisy_patterns = re.compile(
            r"(ad|ads|advert|sponsor|share|social|breadcrumb|popular|"
            r"trending|recommend|related|tags|sidebar|comment|widget)",
            re.I
        )

        elements_to_remove = []
        protected_count = 0
        skipped_top_level = 0
        skipped_shallow = 0

        for el in soup.find_all(True):
            try:
                if not el or not hasattr(el, 'get'):
                    continue
                if id(el) in template_elements_to_protect:
                    protected_count += 1
                    continue
                tag_name = el.name.lower() if hasattr(el, 'name') else ''
                if tag_name in ['body', 'html', 'main']:
                    skipped_top_level += 1
                    continue
                depth = self._get_element_depth(el)
                if depth < 4:
                    skipped_shallow += 1
                    continue

                classes = el.get('class', [])
                classes_str = ' '.join(str(c) for c in classes).lower()
                id_str = str(el.get('id', '')).lower()

                if noisy_patterns.search(classes_str) or noisy_patterns.search(id_str):
                    p_count = len(el.find_all('p'))
                    text_len = len(el.get_text(strip=True))
                    # 使用 AND 條件（對齊 Colab）：兩個條件都成立才移除，避免誤刪文章容器
                    if p_count < 3 and text_len < 400:
                        elements_to_remove.append((el, f"noise_keyword (p={p_count}, len={text_len}, depth={depth})"))
                        continue

                text = el.get_text(" ", strip=True)
                if len(text) > 300:
                    direct_links = el.find_all('a', recursive=False)
                    if len(direct_links) > 5:
                        elements_to_remove.append((el, f"many_links ({len(direct_links)} direct links, depth={depth})"))
                        continue
                    # 只計算明確的欄目導覽標籤（非文章內文），且需 p 數極少
                    p_count_el = len(el.find_all('p'))
                    if p_count_el < 2:
                        category_tags = text.upper().count('ENTERTAINMENT') + \
                                      text.upper().count('BEAUTY') + \
                                      text.upper().count('FASHION') + \
                                      text.upper().count('LIFESTYLE')
                        tag_density = category_tags / max(len(text) / 100, 1)
                        if tag_density > 1.5:
                            elements_to_remove.append((el, f"high_tag_density (density={tag_density:.2f}, tags={category_tags}, p={p_count_el}, len={len(text)}, depth={depth})"))
            except Exception:
                continue

        self._log(f"  → Protected (template): {protected_count}, Skipped top-level: {skipped_top_level}, Skipped shallow: {skipped_shallow}")
        self._log(f"  → Elements marked for removal: {len(elements_to_remove)}")

        for el, reason in elements_to_remove:
            try:
                if el and hasattr(el, 'decompose'):
                    el.decompose()
            except Exception:
                pass

        # Phase 2.1: 構建候選列表
        self._log("\n[Phase 2.1] Building Candidate List")
        candidates = []
        seen_candidates = set()

        def _add_candidate(nodes, source):
            count = 0
            for n in nodes:
                if n and id(n) not in seen_candidates:
                    candidates.append(n)
                    seen_candidates.add(id(n))
                    count += 1
            if count > 0:
                self._log(f"  → Added {count} candidates from: {source}")

        if template_matched:
            tmpl = SITE_TEMPLATES[template_matched]
            for sel in tmpl['selectors']:
                _add_candidate(soup.select(sel), f"Template '{template_matched}': {sel}")

        for sel in MAIN_CONTENT_SELECTORS:
            _add_candidate(soup.select(sel), f"General: {sel}")

        heuristic_nodes = []
        for node in soup.find_all(['article', 'section', 'div', 'main']):
            p_children = node.find_all('p', recursive=True)
            text_len = len(node.get_text(strip=True))
            if len(p_children) >= 3 or text_len > 300:
                heuristic_nodes.append(node)
        _add_candidate(heuristic_nodes, "Heuristic (p>=3 or len>300)")

        self._log(f"\n  → Total unique candidates: {len(candidates)}")

        if not candidates:
            self._log("\n[WARNING] No candidates found! Falling back to full body text")
            body_text = self._clean_text(soup.get_text("\n", strip=True))
            self._log(f"  → Body text length: {len(body_text)} chars")
            return body_text

        # Phase 3: 候選評分
        self._log("\n[Phase 3] Scoring Candidates")
        scored_candidates = []
        filtered_out = []
        for node in candidates:
            if self._looks_like_listing_block(node):
                filtered_out.append(node)
                continue
            score, details = self._calculate_node_score(node, soup)
            if score > 0:
                scored_candidates.append((node, score, details))

        self._log(f"  → Passed filtering: {len(scored_candidates)}, Filtered as listing: {len(filtered_out)}")

        if not scored_candidates:
            self._log("\n[WARNING] All candidates were filtered out! Falling back to full body text")
            body_text = self._clean_text(soup.get_text("\n", strip=True))
            self._log(f"  → Body text length: {len(body_text)} chars")
            return body_text

        scored_candidates.sort(key=lambda x: x[1], reverse=True)

        self._log("\n  [Top 5 Candidates by Score]:")
        for i, (node, score, details) in enumerate(scored_candidates[:5], 1):
            path = self._css_path(node)
            text_len = len(node.get_text(strip=True))
            self._log(f"    {i}. Score: {score:.1f} | Length: {text_len} chars | Path: {path[:100]}...")

        # Phase 4: 置信度計算
        best_node, best_score, _ = scored_candidates[0]
        second_score = scored_candidates[1][1] if len(scored_candidates) > 1 else 0.0
        confidence = self._calculate_confidence(best_score, second_score, best_node)

        self._log(f"\n[Phase 4] Confidence: {confidence:.2%} (threshold {HEURISTIC_CONF_THRESHOLD:.2%})")

        # Phase 5: LLM 輔助（如果需要）
        if confidence < HEURISTIC_CONF_THRESHOLD and HAS_GENAI and self.genai_api_key:
            self._log(f"\n[Phase 5] Low Confidence - Requesting Gemini Assistance")
            selectors = self._ask_gemini_selector(url, soup)
            if selectors:
                best_llm_text, best_llm_score, best_llm_selector = None, 0.0, None
                for sel in selectors:
                    try:
                        node = soup.select_one(sel)
                        if not node:
                            continue
                        if self._looks_like_listing_block(node):
                            continue
                        cleaned = self._clean_text(node.get_text("\n", strip=True))
                        if len(cleaned) < 200:
                            continue
                        score, _ = self._calculate_node_score(node, soup)
                        self._log(f"  → Selector '{sel}' scored {score:.1f}")
                        if score > best_llm_score:
                            best_llm_score, best_llm_text, best_llm_selector = score, cleaned, sel
                    except Exception as e:
                        self._log(f"  → Selector '{sel}' failed: {e}")
                        continue

                if best_llm_text and best_llm_score > best_score:
                    self._log(f"\n  → ✅ Using Gemini's choice (score {best_llm_score:.1f} > {best_score:.1f}): '{best_llm_selector}'")
                    if domain:
                        self.domain_selector_cache[domain] = best_llm_selector
                    return best_llm_text
                else:
                    self._log(f"  → Gemini's suggestions did not improve the result")

        # 返回最佳啟發式結果
        final_content = self._clean_text(best_node.get_text("\n", strip=True))
        self._log(f"\n[Final Selection] Heuristic choice, score {best_score:.1f}, length {len(final_content)} chars")
        self._log("=" * 80)
        self._log("[EXTRACTION COMPLETE]")
        self._log("=" * 80)
        return final_content

    def _fetch_og_meta(self, url: str) -> Dict[str, str]:
        """用社群爬蟲 UA（facebookexternalhit）抓取 og:title / og:description。

        適用 Threads 等對社群 UA 提供 og 文案的站台（連結預覽機制），不需啟動 Chrome。
        Instagram 已封鎖（僅回 og:type），此函式會回傳空 description。
        """
        import urllib.request
        import html as _html
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
                "Accept-Language": ZH_ACCEPT_LANGUAGE,
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", "ignore")

            def _og(prop: str) -> str:
                m = re.search(rf'<meta property="{re.escape(prop)}" content="([^"]*)"', raw)
                if not m:
                    m = re.search(rf'<meta content="([^"]*)" property="{re.escape(prop)}"', raw)
                return _html.unescape(m.group(1)) if m else ""

            return {"title": _og("og:title"), "description": _og("og:description")}
        except Exception as e:
            self._log(f"[OG] 社群 UA 抓取失敗：{e}")
            return {"title": "", "description": ""}

    def scrape(self, url: str, hard_timeout_sec: int = 300,
               keep_driver: bool = False) -> Dict[str, Any]:
        """爬取單一網址，含硬性時限與載入逾時容忍（對齊 Colab v3.8）。

        Args:
            url: 目標網址
            hard_timeout_sec: 每頁硬性時限（秒）。僅計算「爬取」階段，
                          不含 driver 冷啟動（undetected-chromedriver 在 Cloud Run
                          初始化可能達 40–50 秒，不應算進單頁時限）。
            keep_driver: True 時不在結束時 quit driver（供批次重用，省冷啟動）。
                          driver 若 crash 仍會被關閉，下次自動重新初始化。
        """
        self._log(f"====== Starting scrape for: {url} (timeout: {hard_timeout_sec}s) ======")

        # ⭐ 社群貼文（Threads / Instagram）：用社群爬蟲 UA 抓 og 文案，不啟動 Chrome。
        #   Threads 對 facebookexternalhit 提供 og:description（完整文案）；
        #   Instagram 已封鎖 og（僅 og:type），只能回 skipped 並提示 oEmbed/手動。
        url_l = url.lower()
        if any(d in url_l for d in ("threads.com", "threads.net", "instagram.com")):
            og = self._fetch_og_meta(url)
            desc = (og.get("description") or "").strip()
            if len(desc) >= 20:
                title = (og.get("title") or "").strip() or url
                content = self._clean_text(f"{title}\n\n{desc}")
                self._log(f"[Social] 由 og:description 取得貼文文案（{len(content)} 字）")
                return {"status": "success", "url": url, "title": title,
                        "content": content, "length": len(content), "source": "og_social"}
            if "instagram.com" in url_l:
                self._log("[Social] Instagram 無 og 文案（Meta 已封鎖）")
                return {"status": "skipped", "url": url,
                        "error": "Instagram 公開貼文已封鎖 og 抓取，需 oEmbed API（Meta token）或登入瀏覽器手動蒐集。"}
            # threads 但無 og：往下走一般流程

        if self.driver is None:
            self._init_driver()

        # ⭐️ deadline 在 driver 初始化「之後」才開始計時，避免冷啟動吃掉時限。
        deadline = time.time() + hard_timeout_sec

        try:
            # ⭐️ [Phase 1] 使用 _open() 重試邏輯（對齊 Colab v3.8）
            self._open(url)
            # ⭐️ 立即在 DOMContentLoaded 後取快照（SSR 初始 HTML）
            # 目的：防止 A Day Magazine / ELLE 等媒體的 auto-advance JS
            # 在 3-7 秒後替換 DOM 內容，讓後續 URL 換頁時有乾淨的初始快照可回退
            dom_snapshot_source = self.driver.page_source
            self._log(f"[Snapshot] DOMContentLoaded 後立即取得初始 DOM 快照（{len(dom_snapshot_source)} chars）")

            # ⭐ 早期偵測 Chrome 連線錯誤頁（<body class="neterror">）：站台連不上時
            #   快速判失敗，避免在錯誤頁上白跑滾動/Gemini 直到逾時，並讓 Tier 3 提早接手。
            if 'class="neterror"' in dom_snapshot_source or 'id="main-frame-error"' in dom_snapshot_source:
                self._log("[Crawler] 偵測到 Chrome 連線錯誤頁（neterror），快速判失敗")
                return {"status": "failed", "url": url,
                        "error": "瀏覽器錯誤頁（站台無法連線，可能 HTTP-only 或被封鎖）",
                        "browser_error": True}

            self._wait_for_content_load()

            if time.time() > deadline:
                raise TimeoutError(f"超過單頁 {hard_timeout_sec}s 時限（載入階段後）")

            # 遮罩處理（OneTrust → Fides → 通用後備），對齊 Colab v3.8
            self._clear_overlays_and_click_cta()

            if time.time() > deadline:
                raise TimeoutError(f"超過單頁 {hard_timeout_sec}s 時限（遮罩處理後）")

            initial_source = self.driver.page_source
            initial_soup = BeautifulSoup(initial_source, 'html.parser')
            if self._is_listing_page(initial_soup):
                # ⭐ 否決誤判：現代媒體（Vogue/GQ 等）的單篇文章頁 JS 渲染後會載入多張
                #   關聯文章 <article> 卡片，觸發「多個 article = 列表頁」誤判。
                #   單篇文章的可靠信號：(a) JSON-LD NewsArticle articleBody ≥200 字，或
                #   (b) og:type=article（內文在 RSC、JSON-LD 為空時的後備信號）。
                jld_check = self._extract_from_json_ld(initial_source)
                is_article_og = ('og:type" content="article' in initial_source
                                 or "og:type' content='article" in initial_source)
                if len(jld_check) >= 200 or is_article_og:
                    reason = f"JSON-LD {len(jld_check)} 字" if len(jld_check) >= 200 else "og:type=article"
                    self._log(f"[Execution Strategy] 列表頁判斷被「{reason}」否決，視為單篇文章。")
                else:
                    self._log("[Execution Strategy] Detected a listing page. Skipping.")
                    return {"status": "skipped", "url": url, "error": "Skipped: URL is an article list/category page."}

            self._log("[Execution Strategy] Detected a single article page. Proceeding with full scroll.")
            url_changed_during_scroll = self._scroll_and_wait_for_full_load(original_url=url)

            if time.time() > deadline:
                raise TimeoutError(f"超過單頁 {hard_timeout_sec}s 時限（滾動階段後）")

            if 'marieclaire.com' in url.lower():
                self._wait_for_marieclaire_content()

            final_url = self.driver.current_url
            if url_changed_during_scroll or final_url != url:
                self._log(f"[WARNING] URL changed after scroll: {url} → {final_url}，回退至 DOMContentLoaded 初始快照")
                # A Day Magazine 等媒體的 auto-advance JS 會在數秒後替換 DOM 內容並改 URL
                # 使用 DOMContentLoaded 後立即拍的快照（含正確 SSR 內容）
                final_source = dom_snapshot_source
                snap_soup = BeautifulSoup(dom_snapshot_source, 'html.parser')
                title_tag = snap_soup.find('title')
                title = title_tag.get_text(strip=True) if title_tag else "No Title"
                self._log(f"[Snapshot] 回退快照標題：{title}")
            else:
                final_source = self.driver.page_source
                title = self.driver.title or "No Title"
            self._log(f"[Extraction] Page loaded. Title: '{title}'. Starting main content analysis.")

            content = self._extract_main_text(final_source, url)

            # 主文過短：依序嘗試 fallback
            # 500 字閾值：DOM 啟發式可能從導覽列/相關文章抽到少量內容（200-500 字），
            # 但實際文章主體仍在 RSC payload 或 JSON-LD 中，故提高閾值讓 fallback 有機會介入。
            if len(content or '') < 500:
                # 1) JSON-LD articleBody（MirrorMedia 等 Next.js 站台的最可靠來源）
                jld_content = self._extract_from_json_ld(final_source)
                if len(jld_content) > len(content or ''):
                    self._log(f"[JSON-LD] 抽到 {len(jld_content)} 字（優於 DOM {len(content or '')} 字），採用")
                    content = jld_content
                # 2) 現代框架序列化 block payload（Next.js RSC / Copilot 等）
                if len(content or '') < 500:
                    block_content = self._extract_from_block_payload(final_source)
                    if len(block_content) > len(content or ''):
                        self._log(f"[Block Payload] 抽到 {len(block_content)} 字（優於 DOM {len(content or '')} 字），採用")
                        content = block_content
                # 3) 仍過短才補 meta description（對齊 Colab v3.8）
                if len(content or '') < 200:
                    content = self._apply_meta_fallback(content or '', final_source)

            if not content:
                return {"status": "failed", "url": url, "error": "Extracted content is empty after full analysis."}

            # 偵測瀏覽器連線錯誤頁（站台連不上時 Chrome 會渲染錯誤頁，不是真內容）。
            # 視為失敗，讓上層分層 fallback（Tier 3 代理）有機會接手。
            if self._looks_like_browser_error_page(content, title):
                self._log(f"[Crawler] 偵測到瀏覽器錯誤頁（站台無法連線），判定失敗：{title}")
                return {"status": "failed", "url": url,
                        "error": "瀏覽器錯誤頁（站台無法連線，可能 HTTP-only 或被封鎖）",
                        "browser_error": True}

            # 裁掉尾部樣板（贊助／APP／版權），所有萃取路徑統一套用
            content = self._trim_trailing_boilerplate(content)

            return {"status": "success", "url": url, "title": title, "content": content, "length": len(content)}

        except TimeoutError as e:
            self._log(f"[Crawler] 硬性時限超過: {e}")
            # C5: 逾時時嘗試保留已載入的部分內容，避免整篇失敗
            try:
                if self.driver:
                    partial_src = self.driver.page_source
                    partial_content = self._extract_main_text(partial_src, url)
                    if len(partial_content or "") < 200:
                        block = self._extract_from_block_payload(partial_src)
                        if len(block) > len(partial_content or ""):
                            partial_content = block
                    if len(partial_content or "") >= 200:
                        title = self.driver.title or "No Title"
                        # 逾時的部分內容也可能是瀏覽器錯誤頁（站台連不上），同樣判失敗讓 Tier 3 接手
                        if self._looks_like_browser_error_page(partial_content, title):
                            self._log(f"[Crawler] 逾時部分內容為瀏覽器錯誤頁，判定失敗：{title}")
                            return {"status": "failed", "url": url,
                                    "error": "瀏覽器錯誤頁（站台無法連線，可能 HTTP-only 或被封鎖）",
                                    "browser_error": True}
                        partial_content = self._trim_trailing_boilerplate(partial_content)
                        self._log(f"[Crawler] 時限超過但保留 {len(partial_content)} 字部分內容")
                        return {
                            "status": "success", "url": url, "title": title,
                            "content": partial_content, "length": len(partial_content),
                            "warning": f"逾時截斷（{hard_timeout_sec}s），內容可能不完整",
                        }
            except Exception as pe:
                self._log(f"[Crawler] 部分結果抽取失敗: {pe}")
            return {"status": "failed", "url": url, "error": str(e)}
        except WebDriverException as e:
            # driver 崩潰（invalid session / chrome crash）：強制關閉，
            # 讓下次 scrape 重新初始化（即使 keep_driver=True）。
            self._log(f"[Crawler] WebDriver 崩潰，將重啟 driver: {e}")
            self._force_close_driver()
            return {"status": "failed", "url": url, "error": f"WebDriver crash: {e}"}
        except Exception as e:
            self._log(f"[Crawler] CRITICAL ERROR during scrape for {url}: {e}")
            traceback.print_exc()
            return {"status": "failed", "url": url, "error": str(e)}
        finally:
            self._log(f"====== Finished scrape for: {url} ======")
            # 批次重用時保留 driver（省冷啟動）；非重用則關閉。
            if self.driver and not keep_driver:
                self._force_close_driver()

    def _force_close_driver(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
