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
]

SITE_TEMPLATES = {
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
    }
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
    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        self.driver = None  # 型別為 uc.Chrome
        self.max_wait_time = 15
        self.scroll_pause_time = 1.5
        self.domain_selector_cache = {}
        self.genai_api_key = None
        self.log_callback = log_callback

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

        # ⭐️ page_load_strategy = "none"：driver.get() 立即返回，不等任何載入完成。
        #    改由 _open() 主動 poll document.readyState 控制等待時間（最多 N 秒），
        #    達 interactive（HTML/主文 DOM 已就緒）即 window.stop()。
        #    原因：eager 仍會等 DOMContentLoaded，重型站（ELLE 等）可達 90 秒；
        #    且 undetected-chromedriver 不理會 set_page_load_timeout，無法靠它中斷。
        options.page_load_strategy = "none"

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

        # none 策略下 driver.get 仍受 page_load_timeout 影響而阻塞；設短（15s）
        # 讓它快返回，改由 _open 的主動 poll（readyState + 內文穩定）控制等待。
        self.driver.set_page_load_timeout(15)
        self.driver.set_script_timeout(15)
        self._log("[INIT] ✓ undetected-chromedriver 已就緒（頁面15s，腳本15s）")
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

    def _open(self, url: str, max_load_wait: int = 45, max_retries: int = 2):
        """打開網頁，採「none 策略 + 主動等待（readyState + 內文穩定）」。

        page_load_strategy="none"，driver.get() 受短 page_load_timeout 限制快返回。
        然後分兩階段主動等待：
          階段1：poll document.readyState 達 interactive/complete（HTML 已解析）。
          階段2：poll document.body.innerText 長度，等它「渲染穩定」
                 （連續多次不再明顯成長）——針對 client-side render 的頁面
                 （如 GQ 等 Next.js 專題頁），文字靠 JS 填入，需等渲染完才抽取。
        達穩定或超過 max_load_wait（45s）即 window.stop() 用當前 DOM。
        僅「真正的網路連線錯誤」才重試。
        """
        for attempt in range(1, max_retries + 1):
            try:
                self._log(f"[載入] 開啟網頁 (嘗試 {attempt}/{max_retries}): {url}")
                try:
                    self.driver.get(url)
                except TimeoutException:
                    # none 策略 + 短 page_load_timeout：get 逾時但頁面仍在載入，繼續等。
                    pass

                start = time.time()

                # 階段1：等 DOM 可用
                while time.time() - start < max_load_wait:
                    try:
                        state = self.driver.execute_script("return document.readyState")
                    except Exception:
                        state = None
                    if state in ("interactive", "complete"):
                        break
                    time.sleep(0.5)

                # 階段2：等內文渲染穩定（針對 JS 動態渲染的頁面）
                last_len = -1
                stable = 0
                while time.time() - start < max_load_wait:
                    try:
                        cur_len = int(self.driver.execute_script(
                            "return (document.body && document.body.innerText) ? document.body.innerText.length : 0"
                        ) or 0)
                    except Exception:
                        cur_len = last_len
                    # 內文已有一定量且相較上次成長 < 3% → 視為穩定
                    if cur_len > 300 and last_len > 0 and cur_len <= last_len * 1.03:
                        stable += 1
                        if stable >= 2:
                            break
                    else:
                        stable = 0
                    last_len = cur_len
                    time.sleep(1)

                try:
                    self.driver.execute_script("window.stop();")
                except Exception:
                    pass
                self._apply_locale_spoofing_js()

                waited = int(time.time() - start)
                self._log(f"[載入] ✓ 內文長度 {last_len}，等待 {waited}s 後停止，使用當前 DOM")
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
        articles = soup.find_all('article', limit=5)
        if len(articles) > 1:
            self._log(f"[Page Type Analysis] Judgement: LISTING PAGE (found {len(articles)} <article> tags).")
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

    def _scroll_and_wait_for_full_load(self):
        self._log("[Scroll & Wait] Starting full page scroll for single article...")
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        stagnant_scrolls = 0
        max_stagnant_scrolls = 3
        while stagnant_scrolls < max_stagnant_scrolls:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(self.scroll_pause_time)
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height > last_height:
                self._log(f"[Scroll & Wait] Page height increased to {new_height}px. Continuing...")
                last_height = new_height
                stagnant_scrolls = 0
            else:
                stagnant_scrolls += 1
                self._log(f"[Scroll & Wait] Page height stable. Stagnant count: {stagnant_scrolls}/{max_stagnant_scrolls}")
        self._log("[Scroll & Wait] Page height has stabilized. Assuming full page load.")

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
        lines = [line.strip() for line in text.splitlines() if line.strip() and len(line.strip()) >= 6]
        return "\n\n".join(lines)

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
        優先 gemini-2.0-flash，失敗回退 gemini-1.5-flash，temperature=0.3。
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
                    model="gemini-2.0-flash",
                    contents=prompt_text,
                    config=types.GenerateContentConfig(temperature=0.3),
                )
            except Exception:
                self._log(f"[LLM] Gemini 2.0 Flash 不可用，改用 1.5 Flash - {url}")
                resp = client.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=prompt_text,
                    config=types.GenerateContentConfig(temperature=0.3),
                )

            text = (getattr(resp, "text", None) or "").strip()
            text = text.replace("```json", "").replace("```", "").strip()
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

        for tmpl_name, tmpl in SITE_TEMPLATES.items():
            if any(ind in url.lower() for ind in tmpl['indicators']):
                template_matched = tmpl_name
                self._log(f"  → ✅ Matched template: '{tmpl_name}'")
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
                    if p_count < 5 or text_len < 800:
                        elements_to_remove.append((el, f"noise_keyword (p={p_count}, len={text_len}, depth={depth})"))
                        continue

                text = el.get_text(" ", strip=True)
                if len(text) > 300:
                    direct_links = el.find_all('a', recursive=False)
                    if len(direct_links) > 5:
                        elements_to_remove.append((el, f"many_links ({len(direct_links)} direct links, depth={depth})"))
                        continue
                    category_tags = text.upper().count('ENTERTAINMENT') + \
                                  text.upper().count('BEAUTY') + \
                                  text.upper().count('FASHION') + \
                                  text.upper().count('LIFESTYLE')
                    tag_density = category_tags / max(len(text) / 100, 1)
                    if tag_density > 1.0:
                        elements_to_remove.append((el, f"high_tag_density (density={tag_density:.2f}, tags={category_tags}, len={len(text)}, depth={depth})"))
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

    def scrape(self, url: str, hard_timeout_sec: int = 150,
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

        # ⭐️ [Phase 1] Dcard 直接跳過（需要登入，改用 Chrome MCP 手動蒐集）
        if "dcard.tw" in url.lower():
            self._log("[Crawler] Dcard URL detected - skipping (requires login).")
            return {
                "status": "skipped",
                "url": url,
                "error": "Skipped: Dcard 需要登入，請改用 Claude Cowork Chrome MCP 手動蒐集。"
            }

        if self.driver is None:
            self._init_driver()

        # ⭐️ deadline 在 driver 初始化「之後」才開始計時，避免冷啟動吃掉時限。
        deadline = time.time() + hard_timeout_sec

        try:
            # ⭐️ [Phase 1] 使用 _open() 重試邏輯（對齊 Colab v3.8）
            self._open(url)
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
                self._log("[Execution Strategy] Detected a listing page. Skipping.")
                return {"status": "skipped", "url": url, "error": "Skipped: URL is an article list/category page."}

            self._log("[Execution Strategy] Detected a single article page. Proceeding with full scroll.")
            self._scroll_and_wait_for_full_load()

            if time.time() > deadline:
                raise TimeoutError(f"超過單頁 {hard_timeout_sec}s 時限（滾動階段後）")

            if 'marieclaire.com' in url.lower():
                self._wait_for_marieclaire_content()

            final_url = self.driver.current_url
            if final_url != url:
                self._log(f"[WARNING] URL changed! Original: {url} | Final: {final_url}")

            final_source = self.driver.page_source
            title = self.driver.title or "No Title"
            self._log(f"[Extraction] Page loaded. Title: '{title}'. Starting main content analysis.")

            content = self._extract_main_text(final_source, url)

            # ⭐️ [Phase 1] 主文過短時補入 meta description（對齊 Colab v3.8）
            if len(content or '') < 200:
                content = self._apply_meta_fallback(content or '', final_source)

            if not content:
                return {"status": "failed", "url": url, "error": "Extracted content is empty after full analysis."}

            return {"status": "success", "url": url, "title": title, "content": content, "length": len(content)}

        except TimeoutError as e:
            self._log(f"[Crawler] 硬性時限超過: {e}")
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
