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

from net_guard import safe_urlopen  # SSRF 安全版 urlopen（逐跳驗 redirect 目標）
from page_classify import (looks_like_browser_error_page,
                            looks_like_http_error_page, looks_like_block_page)
import text_clean
import dom_score
import dom_parse
from site_templates import SITE_TEMPLATES

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

# 廣告/追蹤/分析網域黑名單（CDP Network.setBlockedURLs，wildcard）。
# 這些 script 持續發請求、讓 renderer 永遠忙，是重型 SSR 站（Hearst listicle/gallery）
# 載入卡死的元兇。封掉它們（廣告非內文，不影響抽取），載入大幅加速、不再卡死。
AD_BLOCKLIST = [
    "*doubleclick.net*", "*googlesyndication.com*", "*googleadservices.com*",
    "*google-analytics.com*", "*googletagmanager.com*", "*googletagservices.com*",
    "*pagead2.googlesyndication.com*", "*adservice.google.*", "*g.doubleclick.net*",
    "*connect.facebook.net*", "*facebook.com/tr*", "*facebook.com/plugins*",
    "*scorecardresearch.com*", "*quantserve.com*", "*quantcount.com*",
    "*criteo.com*", "*criteo.net*", "*taboola.com*", "*outbrain.com*",
    "*amazon-adsystem.com*", "*adnxs.com*", "*rubiconproject.com*",
    "*pubmatic.com*", "*openx.net*", "*casalemedia.com*", "*33across.com*",
    "*adsrvr.org*", "*moatads.com*", "*adform.net*", "*smartadserver.com*",
    "*yieldmo.com*", "*indexww.com*", "*media.net*", "*teads.tv*", "*3lift.com*",
    "*hotjar.com*", "*hotjar.io*", "*mixpanel.com*", "*segment.com*", "*segment.io*",
    "*fullstory.com*", "*mouseflow.com*", "*clarity.ms*", "*newrelic.com*",
    "*nr-data.net*", "*sentry.io*", "*onesignal.com*", "*branch.io*",
    "*ads-twitter.com*", "*analytics.tiktok.com*", "*bat.bing.com*",
    "*sail-horizon.com*", "*cdn.ampproject.org*", "*adsafeprotected.com*",
    "*omnitagjs.com*", "*permutive.com*", "*permutive.app*", "*tinypass.com*",
    # 補強（研究 agent：Hearst 站常見 ad/verification/DMP）
    "*doubleverify.com*", "*sharethrough.com*", "*bidswitch.net*", "*chartbeat.com*",
    "*parsely.com*", "*krxd.net*", "*crwdcntrl.net*", "*demdex.net*", "*omtrdc.net*",
    "*2mdn.net*", "*optimizely.com*", "*sundaysky.com*", "*kargo.com*",
    # 封重量級非文字資源（純文字抽取不需要；進一步降載）
    "*.mp4", "*.webm", "*.woff", "*.woff2", "*.ttf",
]

# Firestore 可覆寫/擴充的封鎖清單快取（與內建合併；讀失敗只用內建）。
_AD_BLOCKLIST_CACHE = {"val": None, "ts": 0.0}


def get_ad_blocklist():
    """生效封鎖清單 = 內建 AD_BLOCKLIST + Firestore `system/config.ad_blocklist`（陣列）。
    可不重新部署即增刪封鎖網域；60s 行程快取；Firestore 讀失敗則回退只用內建。"""
    import time
    now = time.time()
    c = _AD_BLOCKLIST_CACHE
    if c["val"] is not None and now - c["ts"] < 60:
        return c["val"]
    extra = []
    try:
        from firebase_admin import firestore
        doc = firestore.client().collection("system").document("config").get()
        if doc.exists:
            v = (doc.to_dict() or {}).get("ad_blocklist")
            if isinstance(v, list):
                extra = [str(x).strip() for x in v if str(x).strip()]
    except Exception:
        extra = []
    merged = list(dict.fromkeys(AD_BLOCKLIST + extra))  # 去重、保序
    c["val"], c["ts"] = merged, now
    return merged

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


# ⭐️ [v3.8] 抽取前要移除的 CMP（Cookie 同意視窗）容器
#    避免 cookie 分類說明文字被誤判為主文；即使遮罩沒成功關閉也能擋住。
HEURISTIC_CONF_THRESHOLD = 0.55

# 抽取字數門檻（集中管理，原本散落 200/300/500）
MIN_LEARNED_CHARS = 300      # 已學/快取選擇器：讀取端採用前的最低字數（防誤學的寬選擇器污染）
MIN_TEMPLATE_CHARS = 300     # 模板選擇器採用門檻
MIN_FALLBACK_TRIGGER = 500   # scrape 層「主文過短」二級後備觸發門檻
MIN_STATIC_CHARS = 400       # 靜態模板抽取（prefer-static / Chrome 崩潰後備）採用門檻
# 已知 SSR（內文在靜態 HTML）且 Chrome 易崩/不必要的網域 → 優先用靜態模板抽取、跳過 Chrome。
#   ETtoday：Chrome 149 在其廣告/JS 重頁觸發 CDP「missing or invalid columnNumber」崩潰；內文本就在靜態 HTML。
PREFER_STATIC_DOMAINS = ("ettoday.net",)


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
        self._proxy_ext_dir = None  # Tier3 代理 auth 擴充臨時目錄（關 driver 時清理）

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
            self._log("[Crawler] Gemini selector 輔助已設定")

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
        # ⭐ 關閉圖片載入（純文字抽取不需要圖；Hearst gallery 數百張圖正是載入慢的主因）。
        #   用 --blink-settings（UC-safe，避開 add_experimental_option prefs 被 UC 處理掉的坑）。
        #   env CRAWLER_DISABLE_IMAGES=0 可關閉此行為。
        if os.environ.get("CRAWLER_DISABLE_IMAGES", "1") != "0":
            options.add_argument("--blink-settings=imagesEnabled=false")
        # 降載：靜音、停背景網路節流、減少 subframe 程序（重型廣告站少開一堆 iframe 程序）。
        options.add_argument("--mute-audio")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-features=site-per-process,IsolateOrigins")

        # 對齊 Colab v3.8：eager 策略（等 DOMContentLoaded，不等所有資源）。
        # Cloud Run 跨國載入較慢，但「不管時間、確保滾到底抓完整內文」優先。
        options.page_load_strategy = "eager"

        # ⭐ SSRF 縱深防禦：開 performance log，供 _assert_safe_remote_ip 取「主文件
        #   實際連到的 remote IP」事後查驗（擋 DNS rebinding / redirect→內網，這條
        #   driver.get 路徑繞過 net_guard.safe_urlopen）。
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        # ⭐ Tier 3：掛載代理（僅 self.proxy_config 有值時；預設不執行）。
        #   保存擴充目錄路徑，driver 關閉時清理（避免 temp 目錄累積）。
        if self.proxy_config:
            try:
                from tiered_fallback import apply_proxy_to_options
                self._proxy_ext_dir = apply_proxy_to_options(
                    options, self.proxy_config, log_fn=self._log)
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
            # ⭐ 封廣告/追蹤網域：斷掉持續發請求、拖住 page-load 的元兇（重型 SSR 站不再卡死）。
            try:
                _blocklist = get_ad_blocklist()
                self.driver.execute_cdp_cmd(
                    "Network.setBlockedURLs", {"urls": _blocklist})
                self._log(f"[INIT] 已封廣告/追蹤網域 {len(_blocklist)} 條")
            except Exception as e:
                self._log(f"[INIT] 廣告封鎖略過: {e}")
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
                self._assert_safe_remote_ip(url)
                self._apply_locale_spoofing_js()
                self._log("[載入] ✓ 網頁已載入（DOMContentLoaded）")
                return
            except UnsupportedSiteError:
                raise  # SSRF 阻擋：直接往上拋，不重試、不容忍
            except TimeoutException:
                # eager 載入逾時：容忍，用已載入的 DOM；不重試（重試一樣慢）。
                self._log("[載入] ⚠️ 載入逾時，使用已載入內容繼續（網路保持，供後續滾動 lazy-load）")
                self._assert_safe_remote_ip(url)  # 逾時仍可能已連上內網主文件，照驗
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

    def _assert_safe_remote_ip(self, url: str):
        """SSRF 縱深防禦：driver.get 後查 Chrome **實際連到的主文件 remote IP**，
        命中內網（含 GCP metadata 169.254.169.254）即丟棄。

        net_guard.safe_urlopen 只擋 urllib 路徑；Chrome 自行解析 DNS、自動跟隨 redirect，
        可被 DNS rebinding（TTL=0 翻內網）或 redirect→內網繞過。這裡用 performance log
        取最後一個 Document 回應的 remoteIPAddress 再驗一次，涵蓋 scrape/extract-images/
        research 三個都走 _open 的端點。

        判不出 IP（無 perf log 能力 / 無 Document 事件）時 fail-open：不誤殺合法爬取
        （入口 URL 已先過 is_safe_url），只在「確定連到內網」時才丟棄。"""
        try:
            from net_guard import is_safe_ip
            logs = self.driver.get_log("performance")
        except Exception:
            return
        main_ip = None
        for entry in logs:
            try:
                msg = json.loads(entry.get("message", "{}")).get("message", {})
                if msg.get("method") != "Network.responseReceived":
                    continue
                params = msg.get("params", {})
                if params.get("type") != "Document":
                    continue
                ip = (params.get("response") or {}).get("remoteIPAddress")
                if ip:
                    main_ip = ip  # 取最後一個 Document（含 redirect 後的最終主文件）
            except Exception:
                continue
        if not main_ip:
            return
        ok, reason = is_safe_ip(main_ip)
        if not ok:
            self._log(f"[SSRF] ⛔ 阻擋：主文件實際連到內網位址 {main_ip}（{reason}）")
            raise UnsupportedSiteError(f"SSRF 阻擋：{url} 解析至內網位址 {main_ip}")

    def _apply_meta_fallback(self, content: str, html: str) -> str:
        return dom_parse.apply_meta_fallback(content, html, self._log)
    def _extract_from_json_ld(self, html: str) -> str:
        return dom_parse.extract_from_json_ld(html, self._log)
    def _quick_content_len(self, source: str) -> int:
        return dom_parse.quick_content_len(source, self._log)
    def _extract_from_block_payload(self, html: str) -> str:
        return dom_parse.extract_from_block_payload(html, self._log)
    def _clear_overlays_and_click_cta(self, rounds: int = 3,
                                      skip_generic_fallback: bool = False):
        """遮罩處理：對齊 Colab v3.8。
        順序：OneTrust（AllowAll JS → 按鈕）→ Fides（JS API）→ 通用點擊後備。

        skip_generic_fallback=True 時略過「通用後備邏輯」（對所有 button/a 跑 execute_script 點擊）。
        該步對廣告無限載入、renderer 持續忙碌的重 SPA（如 HK 時尚站）會卡死；模板/已知容器
        命中時其實不需要它（站台已知、且實測無遮罩），故略過以免卡住。OneTrust/Fides 仍照處理。
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
        if skip_generic_fallback:
            self._log("[遮罩處理] 模板/已知容器命中，略過通用後備邏輯（避免重 SPA 卡死）")
            return
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
        return dom_parse.is_listing_page(soup, self._log)
    def _scroll_and_wait_for_full_load(self, max_scrolls: int = 60, original_url: str = None,
                                       deadline: float = None):
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
            # 時限收斂：剩餘時間 <30s 即停止滾動進抽取（深滾 60×1.5s≈90s 可能吃滿單頁時限，
            # 重型 lazy gallery 尤甚）。確保留時間給抽取，不被滾動耗盡。
            if deadline is not None and time.time() > deadline - 30:
                self._log("[Scroll & Wait] 接近時限，停止捲動、保留時間給抽取。")
                break
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
        # 純文字清理已抽至 text_clean.clean_text（薄方法委派，呼叫點不變）。
        return text_clean.clean_text(text)

    def _trim_trailing_boilerplate(self, content: str, min_keep: int = 150) -> str:
        # 文末樣板裁切已抽至 text_clean.trim_trailing_boilerplate（薄方法委派，呼叫點不變）。
        return text_clean.trim_trailing_boilerplate(content, min_keep, self._log)

    def _reg_host(self, h: str) -> str:
        """取可註冊網域（末兩段），用於跨站漂移比對。"""
        h = (h or "").lower().split(':')[0]
        parts = h.split('.')
        return '.'.join(parts[-2:]) if len(parts) >= 2 else h

    def _page_cross_domain_drift(self, url: str) -> bool:
        """渲染後當前頁是否跨站漂移到與目標 url 不同的可註冊網域（cloaking 信號）。"""
        try:
            from urllib.parse import urlparse
            cur = self.driver.current_url if self.driver else url
            return self._reg_host(urlparse(cur).hostname or "") != self._reg_host(urlparse(url).hostname or "")
        except Exception:
            return False

    def _css_path(self, el) -> str:
        return dom_score.css_path(el)
    def _get_element_depth(self, el) -> int:
        return dom_score.get_element_depth(el)
    def _build_dom_summary(self, soup: BeautifulSoup, max_count: int = 150) -> List[Dict[str, Any]]:
        return dom_score.build_dom_summary(soup, max_count)
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
                self._log(f"[LLM] gemini-2.5-flash 不可用，改用 gemini-2.5-flash-lite - {url}")
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

    def _looks_like_listing_block(self, node) -> bool:
        return dom_score.looks_like_listing_block(node, self._log)
    def _looks_like_cookie_banner(self, text: str, node=None) -> bool:
        return dom_score.looks_like_cookie_banner(text, node)
    def _calculate_node_score(self, node, soup: BeautifulSoup) -> Tuple[float, Dict[str, float]]:
        return dom_score.calculate_node_score(node, soup, self._log)
    def _calculate_confidence(self, best_score: float, second_score: float, best_node: Any) -> float:
        return dom_score.calculate_confidence(best_score, second_score, best_node)
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
        return dom_parse.remove_cmp_containers(soup, self._log)
    def _extract_main_text(self, html: str, url: str) -> str:
        # P1b 儀表化：本次抽取由哪一階解出（learned/template/structured/heuristic/llm/body_fallback/failed）。
        self.last_resolved_by = "failed"
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

        # Phase 2: 檢查緩存（含 Firestore 持久化的「已學選擇器」，跨重啟/實例記住）
        domain = urlparse(url).netloc
        if domain and domain not in self.domain_selector_cache:
            try:
                from site_learning import load_learned_selectors
                learned = load_learned_selectors().get(domain)
                if learned:
                    self.domain_selector_cache[domain] = learned
                    self._log(f"[SiteLearning] 載入已學選擇器：{domain} → {learned}")
            except Exception:
                pass
        if domain in self.domain_selector_cache:
            sel = self.domain_selector_cache[domain]
            self._log(f"\n[Phase 2] Cache Hit! Using cached selector for domain '{domain}'")
            self._log(f"  → Selector: '{sel}'")
            node = soup.select_one(sel)
            if node:
                content = self._clean_text(node.get_text("\n", strip=True))
                # 讀取端驗證：已學/快取選擇器可能是歷史誤學的寬選擇器（body/列表/cookie 區塊）。
                # 採用前檢查字數 + 非列表 + 非 cookie banner，不達標就 fallthrough 走模板/啟發式，
                # 不再「命中就無條件回傳」（修補選擇器污染整個網域的最大缺口）。
                if (len(content) >= MIN_LEARNED_CHARS
                        and not self._looks_like_listing_block(node)
                        and not self._looks_like_cookie_banner(content, node)):
                    self._log(f"  → ✅ Content extracted: {len(content)} chars")
                    self._log(f"  → Preview: {content[:200]}...")
                    self.last_resolved_by = "learned"
                    return content
                self._log(f"  → ⚠️ Cached selector 命中但內容不合格"
                          f"（{len(content)} 字／列表或 cookie 區塊），改走模板/啟發式")
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
                            self.last_resolved_by = "template"
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

        # Phase 2.05: 結構化資料優先（P2）——比通用啟發式可靠，排在其前（模板未命中/不足時）。
        #   JSON-LD articleBody（多 CMS 內嵌、雜湊站也有）/ [itemprop="articleBody"]。≥500 字才採用
        #   （高於通用 300，避免抓到摘要/teaser）。命中即標 structured 並回傳。
        self._log("\n[Phase 2.05] Structured data (JSON-LD articleBody / itemprop)")
        try:
            sd_text = self._extract_from_json_ld(html)
            if not (sd_text and len(sd_text) >= 500):
                sd_node = soup.select_one('[itemprop="articleBody"]')
                if sd_node:
                    cand = self._clean_text(sd_node.get_text("\n", strip=True))
                    if (len(cand) >= 500 and not self._looks_like_listing_block(sd_node)
                            and not self._looks_like_cookie_banner(cand, sd_node)):
                        sd_text = cand
            if sd_text and len(sd_text) >= 500:
                self._log(f"  → ✅ Structured data hit: {len(sd_text)} chars")
                self.last_resolved_by = "structured"
                return sd_text
            self._log("  → no sufficient structured data, continue")
        except Exception as e:
            self._log(f"  → structured data skipped: {e}")

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
            self.last_resolved_by = "body_fallback"
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
            self.last_resolved_by = "body_fallback"
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
                    if domain and not self._page_cross_domain_drift(url):
                        self.domain_selector_cache[domain] = best_llm_selector
                        # ⭐ 爬蟲研究器：把 Gemini 學到的有效選擇器持久化到 Firestore，
                        #   下次（含重啟/其他實例）直接命中，不必再請 Gemini（自我修復）。
                        try:
                            from site_learning import save_learned_selector, detect_cms
                            save_learned_selector(domain, best_llm_selector, url,
                                                  len(best_llm_text), detect_cms(html))
                        except Exception:
                            pass
                    elif domain:
                        # 防 cloaking 污染：頁面已跨站漂移到不同網域，不把該選擇器學回原網域。
                        self._log("  → ⚠️ 頁面已跨站漂移（疑 cloaking），不學此網域選擇器（防污染）")
                    self.last_resolved_by = "llm"
                    return best_llm_text
                else:
                    self._log(f"  → Gemini's suggestions did not improve the result")

        # 返回最佳啟發式結果
        final_content = self._clean_text(best_node.get_text("\n", strip=True))
        self._log(f"\n[Final Selection] Heuristic choice, score {best_score:.1f}, length {len(final_content)} chars")
        self._log("=" * 80)
        self._log("[EXTRACTION COMPLETE]")
        self._log("=" * 80)
        self.last_resolved_by = "heuristic"
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
            with safe_urlopen(req, timeout=15, max_bytes=3_000_000) as resp:
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

    def _ssr_preprobe(self, url: str) -> Dict[str, Any]:
        """開 Chrome 前的輕量探測：抓靜態 HTML，若結構化資料（JSON-LD articleBody /
        RSC block payload）已含足量內文，直接回傳成功 → 完全跳過 Chrome（省 16–40s 冷啟動
        + 滾動）。只信任「結構化」抽取（不靠長度啟發式，避免 nav/側欄文字灌水誤判），
        對台灣多數 SSR 新聞站收益大。回 dict（success）或 None（探測不足，照常走 Chrome）。
        env CRAWLER_SSR_PROBE=0 可停用。"""
        if os.environ.get("CRAWLER_SSR_PROBE", "1") == "0":
            return None
        # 僅對「未知站」探測：已知站（有模板/已學選擇器）Chrome+模板已抽得完整，
        # 不走預探測以免拿到比完整爬取更短的 JSON-LD 摘要而退步。
        if self._content_container_known(url):
            return None
        import urllib.request
        import html as _html
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": DEFAULT_UA, "Accept-Language": ZH_ACCEPT_LANGUAGE})
            with safe_urlopen(req, timeout=12) as resp:
                if "html" not in (resp.headers.get("Content-Type", "") or "").lower():
                    return None
                html_raw = resp.read(3_000_000).decode("utf-8", "ignore")
        except Exception as e:
            self._log(f"[SSR-Probe] 靜態抓取略過（照常走 Chrome）：{e}")
            return None
        # 只信任結構化抽取
        body = self._extract_from_json_ld(html_raw) or ""
        if len(body) < 1000:
            blk = self._extract_from_block_payload(html_raw) or ""
            if len(blk) > len(body):
                body = blk
        content = self._clean_text(body)
        if len(content) < 1000:
            return None
        m = (re.search(r'<meta property="og:title" content="([^"]*)"', html_raw)
             or re.search(r'<title[^>]*>(.*?)</title>', html_raw, re.S | re.I))
        title = _html.unescape(m.group(1).strip()) if m else url
        self._log(f"[SSR-Probe] ✅ 靜態 HTML 結構化內文足量（{len(content)} 字），跳過 Chrome")
        return {"status": "success", "url": url, "title": title,
                "content": content, "length": len(content), "source": "ssr_preprobe"}

    def _static_extract(self, url: str) -> Dict[str, Any]:
        """抓靜態 HTML 並用「模板/啟發式」抽正文（_extract_main_text）。
        用途：(1) prefer-static 網域（SSR、Chrome 易崩）先試靜態跳過 Chrome；
              (2) Chrome 崩潰/失敗後的後備救援。
        足量（≥MIN_STATIC_CHARS）回 result dict（source=static_template），否則 None。"""
        import urllib.request
        import html as _html
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": DEFAULT_UA, "Accept-Language": ZH_ACCEPT_LANGUAGE})
            with safe_urlopen(req, timeout=15) as resp:
                if "html" not in (resp.headers.get("Content-Type", "") or "").lower():
                    return None
                html_raw = resp.read(3_000_000).decode("utf-8", "ignore")
        except Exception as e:
            self._log(f"[StaticExtract] 靜態抓取失敗：{e}")
            return None
        try:
            content = self._extract_main_text(html_raw, url)
        except Exception as e:
            self._log(f"[StaticExtract] 抽取失敗：{e}")
            return None
        if not content or len(content) < MIN_STATIC_CHARS:
            return None
        m = (re.search(r'<meta property="og:title" content="([^"]*)"', html_raw)
             or re.search(r'<title[^>]*>(.*?)</title>', html_raw, re.S | re.I))
        title = _html.unescape(m.group(1).strip()) if m else url
        self._log(f"[StaticExtract] ✅ 靜態模板抽取成功（{len(content)} 字），未用 Chrome")
        return {"status": "success", "url": url, "title": title,
                "content": content, "length": len(content), "source": "static_template"}

    def _neterror_salvage(self, url: str):
        """Chrome 連線錯誤頁（neterror）後的靜態 HTTP 救援＋封鎖判別。

        背景：部分站台（如 WordPress.com/Automattic）在連線層級封鎖資料中心 IP，
        headless Chrome 一載入就回 net error 錯誤頁。此時用「同容器、不同 TCP/UA」的
        靜態 HTTP 再試一次，能把兩種情況分開，避免一律當可重試 failure 而無限重爬。

        回傳 (verdict, result)：
          ('ok', dict)        靜態 HTTP 抽得到內文 → 直接採用（Chrome 被擋但站台可達）。
          ('blocked', None)   連靜態 HTTP 都連不上（連線層級失敗）→ 資料中心 IP 疑似被封 → 需手動。
          ('reachable', None) 站台可連（含 HTTP 4xx/5xx）但抽不到內文 → 一般失敗（可重試）。
        """
        import urllib.request
        import urllib.error
        import socket
        import html as _html
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": DEFAULT_UA, "Accept-Language": ZH_ACCEPT_LANGUAGE})
            with safe_urlopen(req, timeout=15) as resp:
                ctype = (resp.headers.get("Content-Type", "") or "").lower()
                html_raw = resp.read(3_000_000).decode("utf-8", "ignore") if "html" in ctype else ""
        except urllib.error.HTTPError as e:
            # 有 HTTP 狀態碼（403/404/5xx）＝站台可連、只是擋此請求或無此頁 → 一般失敗（非 IP 封鎖）
            self._log(f"[Neterror救援] 靜態抓取 HTTP {e.code} → 站台可連，判一般失敗")
            return ("reachable", None)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            # 連線層級失敗（連不上/逾時/被 reset）＝資料中心 IP 疑似被站台封鎖 → 需手動，重爬無益
            self._log(f"[Neterror救援] 靜態抓取連線層級失敗（{e}）→ 疑似封鎖資料中心 IP")
            return ("blocked", None)
        if not html_raw:
            return ("reachable", None)
        try:
            content = self._trim_trailing_boilerplate(self._extract_main_text(html_raw, url) or "")
        except Exception as e:
            self._log(f"[Neterror救援] 靜態抽取失敗：{e}")
            content = ""
        if (content and len(content) >= 200
                and not looks_like_block_page(content)
                and not looks_like_browser_error_page(content)):
            m = (re.search(r'<meta property="og:title" content="([^"]*)"', html_raw)
                 or re.search(r'<title[^>]*>(.*?)</title>', html_raw, re.S | re.I))
            title = _html.unescape(m.group(1).strip()) if m else url
            self._log(f"[Neterror救援] ✅ 靜態 HTTP 抽回 {len(content)} 字（Chrome 被擋但站台可連）")
            return ("ok", {"status": "success", "url": url, "title": title,
                           "content": content, "length": len(content), "source": "neterror_salvage"})
        return ("reachable", None)

    # ──────────────────────────────────────────────────────────────────
    # YouTube：Tier 1（oEmbed/og 取標題+說明）+ Tier 2（Gemini 影片理解取口白）
    # ──────────────────────────────────────────────────────────────────
    def _fetch_youtube_oembed(self, url: str) -> Dict[str, str]:
        """YouTube oEmbed：取影片標題與頻道名（免 token）。"""
        import urllib.request
        import urllib.parse
        try:
            api = ("https://www.youtube.com/oembed?url="
                   + urllib.parse.quote(url, safe="") + "&format=json")
            with safe_urlopen(api, timeout=12, max_bytes=1_000_000) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            return {"title": d.get("title", ""), "author": d.get("author_name", "")}
        except Exception as e:
            self._log(f"[YouTube] oEmbed 失敗：{e}")
            return {"title": "", "author": ""}

    def _youtube_transcript_via_gemini(self, url: str) -> str:
        """Tier 2：用 Gemini 影片理解取得影片口白/旁白逐字稿。

        需 ENABLE_YOUTUBE_TRANSCRIPT=1 且有 GENAI key（成本控制，預設關閉）。
        Gemini 2.x 原生支援 YouTube URL：以 file_data(file_uri=YouTube URL) 傳入。
        """
        if os.environ.get("ENABLE_YOUTUBE_TRANSCRIPT", "") != "1":
            return ""
        if not (HAS_GENAI and self.genai_api_key):
            return ""
        try:
            client = genai.Client(api_key=self.genai_api_key)
            prompt = ("請將這支影片的口白／旁白完整內容整理成繁體中文純文字逐字稿，"
                      "包含講者實際說的重點與資訊，不要加任何說明、標題或時間戳。")
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=types.Content(parts=[
                    types.Part(file_data=types.FileData(file_uri=url)),
                    types.Part(text=prompt),
                ]),
            )
            text = (getattr(resp, "text", None) or "").strip()
            self._log(f"[YouTube] Gemini 影片口白 {len(text)} 字")
            return text
        except Exception as e:
            self._log(f"[YouTube] Gemini 影片分析失敗：{e}")
            return ""

    def _scrape_youtube(self, url: str) -> Dict[str, Any]:
        """YouTube 影片資料化：標題 + 頻道 + 影片說明（Tier 1）+ 口白（Tier 2 Gemini）。"""
        self._log(f"[YouTube] 處理影片：{url}")
        oe = self._fetch_youtube_oembed(url)
        og = self._fetch_og_meta(url)  # og:description = 影片說明摘要
        title = (oe.get("title") or og.get("title") or "YouTube 影片").strip()
        desc = (og.get("description") or "").strip()

        parts = [title]
        if oe.get("author"):
            parts.append(f"頻道：{oe['author']}")
        if desc:
            parts.append(f"影片說明：\n{desc}")

        transcript = self._youtube_transcript_via_gemini(url)
        if transcript:
            parts.append(f"影片口白：\n{transcript}")

        content = self._clean_text("\n\n".join(p for p in parts if p))
        if len(content) < 20:
            return {"status": "failed", "url": url,
                    "error": "YouTube 影片無法取得標題/說明（影片可能私人或不存在）"}
        return {"status": "success", "url": url, "title": title,
                "content": content, "length": len(content),
                "source": "youtube" + ("+transcript" if transcript else "")}

    # Cloudflare 挑戰頁特徵（標題/內文）。命中代表正在「請稍候」驗證，需等待自動通過。
    _CF_CHALLENGE_MARKERS = (
        "請稍候", "Just a moment", "Checking your browser", "Checking your connection",
        "需要確認您的連線是安全的", "Verifying you are human", "Attention Required",
        "Enable JavaScript and cookies",
    )

    def _wait_for_cloudflare_clearance(self, max_wait: int = 18) -> bool:
        """偵測 Cloudflare 挑戰頁，等待 undetected-chromedriver 自動通過。

        挑戰頁（「請稍候」/「Just a moment」）會在 JS 挑戰通過後自動跳轉真內容。
        residential proxy（Tier 3）+ 真實瀏覽器指紋時最可能通過；datacenter IP 多半過不了。
        回傳 True = 偵測到挑戰且已通過（內容已變）；False = 無挑戰或未通過。
        """
        def _is_challenge() -> bool:
            try:
                title = self.driver.title or ""
                src = (self.driver.page_source or "")[:6000]
            except Exception:
                return False
            return any(m in title or m in src for m in self._CF_CHALLENGE_MARKERS)

        if not _is_challenge():
            return False
        self._log("[Cloudflare] 偵測到挑戰頁，等待自動通過（最多 %ds）..." % max_wait)
        end = time.time() + max_wait
        while time.time() < end:
            time.sleep(3)
            if not _is_challenge():
                self._log("[Cloudflare] ✓ 挑戰已通過，取得真實內容")
                return True
        self._log("[Cloudflare] ⚠️ 挑戰未在時限內通過（可能 IP 被封或需互動驗證）")
        return False

    def _content_container_known(self, url: str) -> bool:
        """滾動前判斷：內容容器是否已知（模板 indicator 命中、或已學/快取選擇器）。

        已知容器代表內文在固定 DOM 容器內，**不需深滾整頁**（深滾只為觸發 lazy-load
        與 infinity feed，對固定容器無益且耗記憶體）→ 淺滾即可。
        """
        url_lower = url.lower()
        for tmpl in SITE_TEMPLATES.values():
            for ind in tmpl.get('indicators', []):
                if ind in url_lower:
                    return True
        domain = urlparse(url).netloc
        if domain and domain in self.domain_selector_cache:
            return True
        try:
            from site_learning import load_learned_selectors
            if domain and load_learned_selectors().get(domain):
                return True
        except Exception:
            pass
        return False

    def _discover_selector_on_initial_dom(self, url: str, source: str) -> bool:
        """未知站「模板判別優先」：滾動前先用 Gemini 對初始 DOM 找內容選擇器。

        找到並驗證（≥300 字）→ 存入快取 + 學習庫（site_learning），回 True；
        之後 _extract_main_text 會直接命中快取選擇器，且本次可只淺滾。
        沒有 Gemini key 或找不到 → 回 False（退回原本深滾流程）。
        """
        if not (HAS_GENAI and self.genai_api_key):
            return False
        try:
            soup = BeautifulSoup(source, 'html.parser')
            self._remove_cmp_containers(soup)
            selectors = self._ask_gemini_selector(url, soup)
            domain = urlparse(url).netloc
            best, best_len = None, 0
            for sel in (selectors or []):
                try:
                    node = soup.select_one(sel)
                    if node:
                        txt = self._clean_text(node.get_text("\n", strip=True))
                        if len(txt) > best_len:
                            best_len, best = len(txt), sel
                except Exception:
                    continue
            if best and best_len >= 300:
                self.domain_selector_cache[domain] = best
                try:
                    from site_learning import save_learned_selector, detect_cms
                    save_learned_selector(domain, best, url, best_len, detect_cms(source))
                except Exception:
                    pass
                self._log(f"[SiteLearning] 滾動前即時學會選擇器：{domain} → '{best}'（{best_len} 字）")
                return True
        except Exception as e:
            self._log(f"[SiteLearning] 初始 DOM 選擇器探詢失敗（回退深滾）：{e}")
        return False

    def scrape(self, url: str, hard_timeout_sec: int = 300,
               keep_driver: bool = False, force_listing: bool = False) -> Dict[str, Any]:
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
        self.last_resolved_by = ""  # P1b：本次抽取方法（_extract_main_text 會設；early-exit 路徑保持空）

        url_l = url.lower()

        # ⭐ YouTube 影片：Tier 1 取標題+說明（oEmbed/og，免 token）；
        #   Tier 2（env ENABLE_YOUTUBE_TRANSCRIPT=1 且有 GENAI key）用 Gemini 影片理解取口白。
        if any(d in url_l for d in ("youtube.com/watch", "youtu.be/", "youtube.com/shorts", "m.youtube.com/watch")):
            return self._scrape_youtube(url)

        # ⭐ 社群貼文（Threads / Instagram）：用社群爬蟲 UA 抓 og 文案，不啟動 Chrome。
        #   Threads 對 facebookexternalhit 提供 og:description（完整文案）；
        #   Instagram 已封鎖 og（僅 og:type），只能回 skipped 並提示 oEmbed/手動。
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

        # ⭐ prefer-static：已知 SSR 且 Chrome 易崩的網域（如 ETtoday）→ 先用靜態模板抽取、
        #   成功就跳過 Chrome（避開 Chrome 149 的 CDP 崩潰、且更快）。
        if any(d in url_l for d in PREFER_STATIC_DOMAINS):
            st = self._static_extract(url)
            if st is not None:
                return st
            self._log("[Crawler] prefer-static 抽取不足，回退 Chrome")

        # ⭐ SSR 輕量預探測：開 Chrome 前先抓靜態 HTML，若結構化內文（JSON-LD/RSC）已足量
        #   → 直接回傳、完全跳過 Chrome（最省成本、避免深滾卡死）。探測不足才走 Chrome。
        ssr = self._ssr_preprobe(url)
        if ssr is not None:
            return ssr

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
                self._log("[Crawler] 偵測到 Chrome 連線錯誤頁（neterror）→ 靜態 HTTP 救援/封鎖判別")
                verdict, salvaged = self._neterror_salvage(url)
                if salvaged:
                    return salvaged
                if verdict == "blocked":
                    # 瀏覽器與靜態抓取皆連不上 → 資料中心 IP 疑似被站台封鎖（重爬無益）→ 標需手動
                    return {"status": "skipped", "url": url,
                            "error": "站台疑似封鎖資料中心 IP（瀏覽器與靜態抓取皆無法連線），"
                                     "需手動爬取（建議用 Claude Cowork / 住宅網路手動匯入貼上）",
                            "cloaked": True, "needs_manual": True}
                return {"status": "failed", "url": url,
                        "error": "瀏覽器錯誤頁（站台無法連線，可能 HTTP-only 或被封鎖）",
                        "browser_error": True}

            self._wait_for_content_load()

            # ⭐ Cloudflare 挑戰頁（Dcard 等）：給 undetected-chromedriver 時間自動通過 JS 挑戰
            #   （挑戰頁數秒後自動跳轉真內容）。通過後重取快照。residential proxy（Tier 3）時最有效。
            if self._wait_for_cloudflare_clearance():
                dom_snapshot_source = self.driver.page_source

            if time.time() > deadline:
                raise TimeoutError(f"超過單頁 {hard_timeout_sec}s 時限（載入階段後）")

            # 遮罩處理（OneTrust → Fides → 通用後備），對齊 Colab v3.8。
            # 模板/已學選擇器命中（已知站）→ 略過會卡死的通用後備（重 SPA 廣告拖住 renderer）。
            self._clear_overlays_and_click_cta(
                skip_generic_fallback=self._content_container_known(url))

            if time.time() > deadline:
                raise TimeoutError(f"超過單頁 {hard_timeout_sec}s 時限（遮罩處理後）")

            initial_source = self.driver.page_source
            initial_soup = BeautifulSoup(initial_source, 'html.parser')
            # 模板/已學選擇器命中＝已知文章站 → 不做列表頁誤判（esquirehk 等文章頁有多個 <article>
            #   關聯卡片會被誤判成列表頁而 skip）。只對未知站做列表頁檢查。
            if not self._content_container_known(url) and self._is_listing_page(initial_soup):
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
                elif force_listing:
                    self._log("[Execution Strategy] 列表頁——但 force_listing=True，強制抽取（不略過）。")
                else:
                    self._log("[Execution Strategy] Detected a listing page. Skipping.")
                    return {"status": "skipped", "url": url, "error": "Skipped: URL is an article list/category page."}

            # ⭐ 滾動策略（模板判別優先，治本省記憶體）：
            #   1) 初始 DOM 已足量 → 淺滾。
            #   2) 內容容器已知（模板/已學選擇器）→ 淺滾（固定容器不必深滾整頁）。
            #   3) 未知站 → 先用 Gemini 對初始 DOM 即時學選擇器；學到 → 淺滾、否則才深滾。
            early_len = self._quick_content_len(initial_source)
            if early_len >= 1200:
                self._log(f"[Execution Strategy] 初始 DOM 已有足量內容（{early_len} 字），淺滾跳過深度滾動。")
                url_changed_during_scroll = self._scroll_and_wait_for_full_load(max_scrolls=4, original_url=url)
            elif self._content_container_known(url):
                self._log("[Execution Strategy] 內容容器已知（模板/已學選擇器），淺滾跳過深度滾動。")
                url_changed_during_scroll = self._scroll_and_wait_for_full_load(max_scrolls=4, original_url=url)
            elif self._discover_selector_on_initial_dom(url, initial_source):
                self._log("[Execution Strategy] 未知站：滾動前即時學會內容選擇器，淺滾。")
                url_changed_during_scroll = self._scroll_and_wait_for_full_load(max_scrolls=4, original_url=url)
            else:
                self._log("[Execution Strategy] Detected a single article page. Proceeding with full scroll.")
                url_changed_during_scroll = self._scroll_and_wait_for_full_load(
                    original_url=url, deadline=deadline)

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
                # 2.5) DOMContentLoaded 初始快照（SSR 內容）：A Day Magazine 等用 fullPage.js /
                #     auto-advance 的站台，JS 渲染後會清空內容，但初始快照保有 SSR 原文。
                if len(content or '') < 500 and dom_snapshot_source and dom_snapshot_source != final_source:
                    snap_content = self._extract_main_text(dom_snapshot_source, url)
                    if len(snap_content) > len(content or ''):
                        self._log(f"[Snapshot] 從 DOMContentLoaded 初始快照抽到 {len(snap_content)} 字（優於 {len(content or '')} 字），採用")
                        content = snap_content
                # 3) 仍過短才補 meta description（對齊 Colab v3.8）
                if len(content or '') < 200:
                    content = self._apply_meta_fallback(content or '', final_source)

            if not content:
                return {"status": "failed", "url": url, "error": "Extracted content is empty after full analysis."}

            # 偵測瀏覽器連線錯誤頁（站台連不上時 Chrome 會渲染錯誤頁，不是真內容）。
            # 視為失敗，讓上層分層 fallback（Tier 3 代理）有機會接手。
            if looks_like_browser_error_page(content, title):
                self._log(f"[Crawler] 偵測到瀏覽器錯誤頁（站台無法連線），判定失敗：{title}")
                return {"status": "failed", "url": url,
                        "error": "瀏覽器錯誤頁（站台無法連線，可能 HTTP-only 或被封鎖）",
                        "browser_error": True}

            # HTTP 錯誤頁偵測（如 403 Forbidden / 404）：內容極短且符合錯誤特徵 → 判失敗，
            # 不讓「403 Forbidden」這種 28 字錯誤頁被當成功污染分析。
            if len(content) < 150 and looks_like_http_error_page(content, title):
                self._log(f"[Crawler] 偵測到 HTTP 錯誤頁（{(title or content)[:40]}），判定失敗")
                return {"status": "failed", "url": url,
                        "error": f"HTTP 錯誤頁：{(title or content)[:60]}",
                        "browser_error": True}

            # 裁掉尾部樣板（贊助／APP／版權），所有萃取路徑統一套用
            content = self._trim_trailing_boilerplate(content)

            # 反爬偵測：cloaking 跨站漂移 / 封鎖頁 → 標記『需手動爬取』，不把誤導/封鎖內容當文章污染分析。
            #（① 住宅代理破解見 tiered_fallback；開關 tier3_enabled 開啟後才於升級階段自動重抓。）
            drift = ((url_changed_during_scroll or final_url != url)
                     and self._reg_host(urlparse(final_url).hostname or "")
                         != self._reg_host(urlparse(url).hostname or ""))
            if drift or looks_like_block_page(content, title):
                reason = (f"內容飄移到不同網域（{urlparse(final_url).hostname}），疑似 cloaking 反爬"
                          if drift else "疑似反爬封鎖／驗證頁")
                self._log(f"[反爬] {reason} → 標記需手動爬取")
                return {"status": "skipped", "url": url, "title": title,
                        "error": f"{reason}，需手動爬取（建議用手動匯入貼上真人版內容）",
                        "cloaked": True, "needs_manual": True}

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
                        if looks_like_browser_error_page(partial_content, title):
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
            # driver 崩潰（invalid session / chrome crash，如 Chrome 149「missing or invalid
            # columnNumber」）：強制關閉讓下次重啟；並用靜態模板抽取後備救援（SSR 站可救回）。
            self._log(f"[Crawler] WebDriver 崩潰，將重啟 driver: {e}")
            self._force_close_driver()
            st = self._static_extract(url)
            if st is not None:
                self._log("[Crawler] Chrome 崩潰 → 靜態模板後備成功救回")
                return st
            return {"status": "failed", "url": url, "error": f"WebDriver crash: {e}"}
        except Exception as e:
            self._log(f"[Crawler] CRITICAL ERROR during scrape for {url}: {e}")
            traceback.print_exc()
            st = self._static_extract(url)
            if st is not None:
                self._log("[Crawler] Chrome 例外 → 靜態模板後備成功救回")
                return st
            return {"status": "failed", "url": url, "error": str(e)}
        finally:
            self._log(f"====== Finished scrape for: {url} ======")
            # 批次重用時保留 driver（省冷啟動）；非重用則關閉。
            if self.driver and not keep_driver:
                self._force_close_driver()

    def _cleanup_proxy_ext(self):
        """清理 Tier3 代理 auth 擴充的臨時目錄（避免累積）。"""
        if self._proxy_ext_dir:
            try:
                import shutil
                shutil.rmtree(self._proxy_ext_dir, ignore_errors=True)
            except Exception:
                pass
            self._proxy_ext_dir = None

    def _force_close_driver(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self._cleanup_proxy_ext()

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self._cleanup_proxy_ext()
