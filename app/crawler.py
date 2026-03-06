# -*- coding: utf-8 -*-
import os
import re
import time
import json
import traceback
from typing import Optional, Tuple, Dict, Any, List, Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup
# 使用 undetected-chromedriver 代替標準 selenium
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# [FIX] 導入 selenium_stealth 用於 Nix 環境的偽裝
try:
    from selenium_stealth import stealth
except ImportError:
    stealth = None

# LLM Support
try:
    import google.generativeai as genai
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
            '.articleContent',           # 原有
            'div.articleContent',        # 原有
            '[id^="content"]',           # 新增：ID 以 "content" 開頭（如 content80407）
            '#container80407 .articleContent',  # 容器內的 articleContent
            '.articleContainer .articleContent',  # 容器內的 articleContent  
            '.article-content',          # 原有
            'article .content',          # 新增
            '[class*="article"][class*="content"]',  # 新增：包含 article 和 content 的 class
            '.post-content',             # 新增
            '[itemprop="articleBody"]',  # 新增：語義化標記
            'main article',              # 新增：主區域內的 article
            'article'                    # 新增：最後備選
        ]
    }
}

HEURISTIC_CONF_THRESHOLD = 0.55

class HeadlessCrawler:
    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        self.driver = None  # Driver 類型可能是 uc.Chrome 或 webdriver.Chrome
        self.max_wait_time = 15
        self.scroll_pause_time = 1.5
        self.domain_selector_cache = {}
        self.genai_api_key = None
        self.log_callback = log_callback
        
        env_key = os.environ.get("GENAI_API_KEY")
        if env_key:
            self.configure_genai(env_key)

    def _log(self, message: str):
        print(message)
        if self.log_callback:
            try:
                self.log_callback(message)
            except:
                pass

    def configure_genai(self, api_key: str):
        if HAS_GENAI and api_key:
            self.genai_api_key = api_key
            genai.configure(api_key=self.genai_api_key)
            self._log(f"[Crawler] Gemini configured with key: ...{api_key[-4:]}")

    def _init_driver(self):
        """
        初始化 WebDriver。
        實作混合策略：
        1. 若在 Nix 開發環境，使用標準 Selenium + Stealth (避免 UC binary patch 錯誤)。
        2. 若在 Cloud Run (Docker) 環境，使用 undetected-chromedriver (最佳反偵測)。
        """
        chrome_bin = os.environ.get("CHROME_BIN")
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")

        if not chrome_bin:
            raise RuntimeError(
                "找不到 Chrome 執行檔；請確認已安裝 google-chrome 或設定環境變數 CHROME_BIN"
            )

        # 檢測是否為 Nix 環境 (Chrome 路徑包含 /nix/store)
        is_nix_env = "/nix/store" in chrome_bin
        
        if is_nix_env:
            self._log("[INIT] 偵測到 Nix 開發環境，切換至標準 Selenium + Stealth 模式")
            return self._init_standard_selenium_with_stealth(chrome_bin, chromedriver_path)
        else:
            self._log("[INIT] 偵測到生產環境 (Docker)，使用 undetected-chromedriver")
            return self._init_undetected_chromedriver(chrome_bin, chromedriver_path)

    def _init_standard_selenium_with_stealth(self, chrome_bin, chromedriver_path):
        """Nix 環境專用：標準 Selenium + Stealth"""
        options = Options()
        options.binary_location = chrome_bin
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=zh-TW")
        
        service = Service(executable_path=chromedriver_path) if chromedriver_path else Service()
        
        self.driver = webdriver.Chrome(options=options, service=service)
        
        # 應用 Stealth 偽裝
        if stealth:
            stealth(self.driver,
                languages=["zh-TW", "zh", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )
            self._log("[INIT] Selenium Stealth 已啟用")
        else:
            self._log("[WARNING] Selenium Stealth 未安裝，可能容易被偵測")

        # 手動 CDP 設定
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {"Accept-Language": ZH_ACCEPT_LANGUAGE}})
        except Exception as e:
            self._log(f"[INIT] CDP 設定略過: {e}")
            
        return self.driver

    def _init_undetected_chromedriver(self, chrome_bin, chromedriver_path):
        """Cloud Run 環境專用：undetected-chromedriver"""
        options = uc.ChromeOptions()
        options.binary_location = chrome_bin
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=zh-TW")
        options.add_argument(f"--user-agent={DEFAULT_UA}")

        caps = DesiredCapabilities.CHROME.copy()
        caps["pageLoadStrategy"] = "eager"

        uc_params = {
            "options": options,
            "desired_capabilities": caps,
            "browser_executable_path": chrome_bin,
        }
        
        # 即使在 Docker，若有指定 path，也強制使用以避免下載
        if chromedriver_path and os.path.exists(chromedriver_path):
             uc_params["driver_executable_path"] = chromedriver_path

        self.driver = uc.Chrome(**uc_params)
        
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {"Accept-Language": ZH_ACCEPT_LANGUAGE}})
            self.driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Taipei"})
        except Exception as e:
            self._log(f"[INIT] CDP 設定略過: {e}")
            
        self.driver.set_page_load_timeout(25)
        self.driver.set_script_timeout(15)
        self._log("[INIT] ✓ undetected-chromedriver 已就緒")
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

    def _handle_fides_cookie(self):
        """
        處理 Fides Cookie 彈窗（Marie Claire 等網站使用）
        這是 Colab 版本中經過驗證的方法
        """
        try:
            # 等待 Fides API 載入（最多 10 秒）
            self._log("[Cookie] 正在檢測 Fides API...")
            WebDriverWait(self.driver, 10).until(
                lambda d: d.execute_script(
                    "return !!window.Fides && typeof window.Fides.consent === 'object'"
                ),
                "Fides API (window.Fides.consent) 未能在 10 秒內載入"
            )
            
            self._log("[Cookie] ✓ Fides API 已載入，正在讀取 consent keys...")
            
            # 獲取 Fides.consent 物件
            consent_prefs = self.driver.execute_script("return window.Fides.consent")
            
            if not consent_prefs or not isinstance(consent_prefs, dict):
                self._log(f"[Cookie] 無法讀取 Fides.consent: {consent_prefs}")
                return
            
            # 將所有 keys 設為 True（全部同意）
            all_true_prefs = {key: True for key in consent_prefs.keys()}
            self._log(f"[Cookie] 準備 '全部同意' payload (keys: {list(all_true_prefs.keys())})")
            
            # 呼叫 Fides API 儲存偏好
            script = """
            const prefs = arguments[0];
            if (!window.Fides) return 'Fides object missing';
            
            try {
                if (typeof window.Fides.updateConsent === 'function') {
                    // Fides.js >= v2.22.0
                    window.Fides.updateConsent(prefs);
                    return 'Called Fides.updateConsent()';
                } else if (typeof window.Fides.savePreferences === 'function') {
                    // 舊版 Fides
                    window.Fides.savePreferences(prefs);
                    return 'Called Fides.savePreferences()';
                } else {
                    // 備用方案：直接覆寫並隱藏 modal
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
            self._log(f"[Cookie] ✓ Fides API 呼叫完畢: {result}")
            
            # 等待 Fides 遮罩消失
            try:
                # 嘗試等待 iframe 消失
                WebDriverWait(self.driver, 5).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, "iframe[id='fides-iframe'], iframe[title*='Fides']"))
                )
            except TimeoutException:
                # 如果 iframe 沒消失，嘗試等待 overlay 消失
                self._log("[Cookie] iframe 未消失，檢查 #fides-overlay...")
                try:
                    WebDriverWait(self.driver, 3).until(
                        EC.invisibility_of_element_located((By.CSS_SELECTOR, "#fides-overlay"))
                    )
                except TimeoutException:
                    pass  # overlay 也不存在或沒消失，繼續
            
            self._log("[Cookie] ✓ Fides 處理完成")
            
        except TimeoutException:
            self._log("[Cookie] 未檢測到 Fides API，跳過")
        except Exception as e:
            self._log(f"[Cookie] Fides 處理失敗: {e}")
        finally:
            # 確保切回主 DOM
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass


    def _clear_overlays_and_click_cta(self, rounds: int = 3):
        try:
            debug_info = self.driver.execute_script("""
            return {
              url: location.href,
              hasFides: !!window.Fides,
              hasConsent: !!(window.Fides && window.Fides.consent),
              consentType: window.Fides && window.Fides.consent ? typeof window.Fides.consent : null
            };
            """)
            self._log(f"[遮罩處理][DEBUG] Fides 狀態: {debug_info}")
        except Exception as e:
            self._log(f"[遮罩處理][DEBUG] 無法讀取 window.Fides: {e}")
        try:
            onetrust_btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            )
            self._log(f"[遮罩處理] 偵測到 OneTrust，嘗試點擊...")
            self.driver.execute_script("arguments[0].click();", onetrust_btn)
            time.sleep(1.0)
            WebDriverWait(self.driver, 3).until(
                EC.invisibility_of_element_located((By.ID, "onetrust-banner-sdk"))
            )
            self._log(f"[遮罩處理] OneTrust 遮罩已關閉。")
            return 
        except (NoSuchElementException, TimeoutException):
            pass 
        except Exception as e:
            self._log(f"[遮罩處理] OneTrust 處理異常: {e}")
        try:
            self._log("[遮罩處理] 正在等待 Fides API (window.Fides.consent) 載入...")
            WebDriverWait(self.driver, 10).until(
                lambda d: d.execute_script("return !!window.Fides && typeof window.Fides.consent === 'object'"),
                "Fides API (window.Fides.consent) 未能在 10 秒內載入"
            )
            consent_prefs = self.driver.execute_script("return window.Fides.consent")
            if isinstance(consent_prefs, dict):
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
                self._log(f"[遮罩處理] Fides API 呼叫結果: {result}")
                try:
                    WebDriverWait(self.driver, 5).until(
                        EC.invisibility_of_element_located((By.CSS_SELECTOR, "iframe[id='fides-iframe'], iframe[title*='Fides']"))
                    )
                except TimeoutException:
                    self._log("[遮罩處理] iframe 未消失，嘗試檢查 overlay div...")
                    try:
                        WebDriverWait(self.driver, 3).until(
                            EC.invisibility_of_element_located((By.CSS_SELECTOR, "#fides-overlay"))
                        )
                    except: pass
                return
        except (NoSuchElementException, TimeoutException):
            self._log("[遮罩處理] Fides API 未偵測到或載入超時，切換至通用點擊邏輯...")
        except Exception as e:
            self._log(f"[遮罩處理] Fides API 處理異常: {e}")

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
            'article p'  # 至少要有文章段落
        ]
        
        for sel in selectors_to_wait:
            try:
                self._log(f"[Marie Claire] Checking for: {sel}")
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
                self._log(f"[Marie Claire] ✅ Found: {sel}")
                
                # 額外等待一下確保內容完全渲染
                time.sleep(2)
                return True
            except TimeoutException:
                self._log(f"[Marie Claire] ❌ Not found: {sel}")
                continue
        
        self._log("[Marie Claire] ⚠️ No article content selectors matched, proceeding anyway")
        return False

    def _clean_text(self, text: str) -> str:
        if not text: return ""
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
                    if el_id: ident += f"#{el_id}"
                    classes = cur.get('class') if hasattr(cur, 'get') else None
                    if classes and isinstance(classes, (list, tuple)):
                        ident += '.' + '.'.join(str(c) for c in classes[:3])
                    parts.append(ident)
                    cur = cur.parent if hasattr(cur, 'parent') else None
                except Exception:
                    break
                if len(parts) > 20: break
            return ' > '.join(reversed(parts))
        except Exception:
            return "unknown"

    def _get_element_depth(self, el) -> int:
        """計算元素在 DOM 樹中的深度"""
        try:
            depth = 0
            current = el
            while current and hasattr(current, 'parent'):
                depth += 1
                current = current.parent
                if depth > 50: break
            return depth
        except:
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
        if not HAS_GENAI or not self.genai_api_key:
            return []
        self._log(f"[LLM] Asking Gemini for selector: {url}")
        try:
            dom_summary = self._build_dom_summary(soup)
            prompt_sys = (
                "你是資深前端工程師。根據下列 DOM 摘要，判斷最可能代表文章主文的容器，"
                "只回傳 JSON：{\"selector\":\"...\",\"confidence\":0~1,\"alternatives\":[\"...\",\"...\"]}"
            )
            user_payload = {"url": url, "candidates": dom_summary}
            model = genai.GenerativeModel("gemini-1.5-flash", generation_config={"temperature": 0.3})
            resp = model.generate_content(f"{prompt_sys}\n\n{json.dumps(user_payload, ensure_ascii=False)[:30000]}")
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
            if not node: return 0
            depth = 0
            current = node
            while current and hasattr(current, 'parent'):
                depth += 1
                current = current.parent
                if depth > 50: break
            return depth
        except Exception:
            return 5

    def _calculate_paragraph_quality(self, node) -> float:
        try:
            if not node or not hasattr(node, 'find_all'): return 0.0
            paragraphs = node.find_all('p')
            if not paragraphs: return 0.0
            total_score = 0.0
            for p in paragraphs:
                try:
                    text = p.get_text(strip=True)
                    if len(text) < 20: continue
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
        if not text: return 0.0
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        return chinese_chars / max(len(text), 1)

    def _looks_like_listing_block(self, node) -> bool:
        if len(node.find_all('article', recursive=False)) > 3 or len(node.find_all('li', recursive=False)) > 5:
            self._log(f"[Filter] Node disqualified by structure (contains multiple <article> or <li>).")
            return True
        text = node.get_text(" ", strip=True).lower()
        if not text: return False
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
        if not text: return False
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
            if not node or not hasattr(node, 'get_text'): return 0.0, {}
            text = node.get_text("\n", strip=True)
            text_len = len(text)
            if text_len < 100: return 0.0, {}
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
        if best_score >= 1500: score_conf = 1.0
        elif best_score >= 800: score_conf = 0.7 + (best_score - 800) / 700 * 0.3
        else: score_conf = best_score / 800 * 0.7
        structure_conf = 0.5
        try:
            if best_node.find(['h1', 'h2', 'h3']): structure_conf += 0.2
            if best_node.find(['time', '[datetime]']): structure_conf += 0.15
            if len(best_node.find_all('p')) >= 5: structure_conf += 0.15
        except Exception: pass
        structure_conf = min(1.0, structure_conf)
        final_conf = (margin_conf * 0.4 + score_conf * 0.3 + structure_conf * 0.3)
        return final_conf

    def _wait_for_content_load(self):
        try:
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except TimeoutException:
            self._log("[Crawler] Timeout waiting for body")

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
                self._log(f"  → Indicators: {tmpl['indicators']}")
                self._log(f"  → Selectors to try: {tmpl['selectors']}")
                
                for sel in tmpl['selectors']:
                    try:
                        self._log(f"\n  [Trying selector: '{sel}']")
                        node = soup.select_one(sel)
                        
                        if not node:
                            self._log(f"    → ❌ Selector did not match any element")
                            continue
                        
                        # 標記這個元素，保護它不被噪音過濾移除
                        template_elements_to_protect.add(id(node))
                        
                        text = node.get_text("\n", strip=True)
                        self._log(f"    → ✅ Element found!")
                        self._log(f"    → Raw text length: {len(text)} chars")
                        self._log(f"    → Number of <p> tags: {len(node.find_all('p'))}")
                        
                        cleaned = self._clean_text(text)
                        self._log(f"    → Cleaned text length: {len(cleaned)} chars")
                        
                        if len(cleaned) >= 300:
                            self._log(f"    → ✅ SUCCESS! Content is sufficient (>= 300 chars)")
                            self._log(f"    → Caching selector for future use")
                            self._log(f"\n[Content Preview (first 300 chars)]:")
                            self._log(f"{cleaned[:300]}...")
                            self._log("\n" + "=" * 80)
                            self._log("[EXTRACTION COMPLETE - Using Template Selector]")
                            self._log("=" * 80)
                            
                            if domain:
                                self.domain_selector_cache[domain] = sel
                            return cleaned
                        else:
                            self._log(f"    → ⚠️ Content too short (< 300 chars), trying next selector")
                            
                    except Exception as e:
                        self._log(f"    → ❌ Selector failed with error: {e}")
                        continue
                
                self._log(f"\n  → ⚠️ Template selectors did not return sufficient content")
                self._log(f"  → Falling back to general extraction")
                
                # 診斷：輸出實際的 HTML 結構
                self._log(f"\n  [Diagnostic] Analyzing actual HTML structure:")
                
                # 測試所有選擇器並顯示結果
                self._log(f"\n  [Testing all selectors on actual HTML]:")
                for i, test_sel in enumerate(tmpl['selectors'], 1):
                    try:
                        test_node = soup.select_one(test_sel)
                        if test_node:
                            test_text_len = len(test_node.get_text(strip=True))
                            test_p_count = len(test_node.find_all('p'))
                            self._log(f"    {i}. '{test_sel}' → ✅ Found!")
                            self._log(f"       Text: {test_text_len} chars, {test_p_count} <p> tags")
                            if test_text_len > 0:
                                self._log(f"       Preview: {test_node.get_text(strip=True)[:100]}...")
                        else:
                            self._log(f"    {i}. '{test_sel}' → ❌ Not found")
                    except Exception as e:
                        self._log(f"    {i}. '{test_sel}' → ❌ Error: {e}")
                
                # 查找所有包含 "article" 或 "content" 的元素
                self._log(f"\n  [Elements with 'article' or 'content' in class/id]:")
                potential_elements = []
                for el in soup.find_all(True):
                    classes = el.get('class', [])
                    el_id = el.get('id', '')
                    classes_str = ' '.join(str(c) for c in classes).lower() if classes else ''
                    id_str = str(el_id).lower() if el_id else ''
                    
                    if 'article' in classes_str or 'content' in classes_str or \
                       'article' in id_str or 'content' in id_str:
                        text_len = len(el.get_text(strip=True))
                        p_count = len(el.find_all('p'))
                        if text_len > 100:  # 只看有內容的元素
                            potential_elements.append({
                                'tag': el.name,
                                'classes': classes,
                                'id': el_id,
                                'text_len': text_len,
                                'p_count': p_count,
                                'preview': el.get_text(strip=True)[:100]
                            })
                
                # 按文本長度排序
                potential_elements.sort(key=lambda x: x['text_len'], reverse=True)
                
                if potential_elements:
                    self._log(f"  → Found {len(potential_elements)} relevant elements:")
                    for i, elem in enumerate(potential_elements[:5], 1):
                        self._log(f"\n    {i}. <{elem['tag']}>")
                        self._log(f"       Classes: {elem['classes']}")
                        self._log(f"       ID: '{elem['id']}'")
                        self._log(f"       Stats: {elem['text_len']} chars, {elem['p_count']} <p> tags")
                        self._log(f"       Preview: {elem['preview']}")
                else:
                    self._log(f"  → Found 0 elements with 'article' or 'content'")
                    self._log(f"  → This means the HTML structure is completely different!")
                
                # 查找所有長文本的 div（可能的內容容器）
                self._log(f"\n  [Top 5 div elements by text length (>500 chars, >=3 paragraphs)]:")
                all_divs = []
                for div in soup.find_all('div'):
                    text_len = len(div.get_text(strip=True))
                    p_count = len(div.find_all('p'))
                    if text_len > 500 and p_count >= 3:
                        all_divs.append({
                            'classes': div.get('class', []),
                            'id': div.get('id', ''),
                            'text_len': text_len,
                            'p_count': p_count,
                            'preview': div.get_text(strip=True)[:100]
                        })
                
                all_divs.sort(key=lambda x: x['text_len'], reverse=True)
                
                if all_divs:
                    self._log(f"  → Found {len(all_divs)} divs matching criteria:")
                    for i, div in enumerate(all_divs[:5], 1):
                        self._log(f"\n    {i}. DIV")
                        self._log(f"       Classes: {div['classes']}")
                        self._log(f"       ID: '{div['id']}'")
                        self._log(f"       Stats: {div['text_len']} chars, {div['p_count']} <p> tags")
                        self._log(f"       Preview: {div['preview']}")
                    
                    # 生成建議選擇器
                    self._log(f"\n  [Suggested selectors based on found elements]:")
                    suggested = []
                    for i, div in enumerate(all_divs[:3], 1):
                        if div['id']:
                            suggested.append(f"#{div['id']}")
                            self._log(f"    Try: '#{div['id']}'")
                        elif div['classes']:
                            class_sel = '.' + '.'.join(str(c) for c in div['classes'])
                            suggested.append(class_sel)
                            self._log(f"    Try: '{class_sel}'")
                else:
                    self._log(f"  → Found 0 divs with >500 chars and >=3 paragraphs")
                    self._log(f"  → Trying broader search...")
                    
                    # 更寬鬆的搜索
                    broader_divs = []
                    for div in soup.find_all('div'):
                        text_len = len(div.get_text(strip=True))
                        if text_len > 200:  # 降低閾值
                            broader_divs.append({
                                'classes': div.get('class', []),
                                'id': div.get('id', ''),
                                'text_len': text_len,
                                'p_count': len(div.find_all('p')),
                                'preview': div.get_text(strip=True)[:100]
                            })
                    
                    broader_divs.sort(key=lambda x: x['text_len'], reverse=True)
                    
                    if broader_divs:
                        self._log(f"\n  [Broader search: Top 5 divs with >200 chars]:")
                        for i, div in enumerate(broader_divs[:5], 1):
                            self._log(f"\n    {i}. DIV")
                            self._log(f"       Classes: {div['classes']}")
                            self._log(f"       ID: '{div['id']}'")
                            self._log(f"       Stats: {div['text_len']} chars, {div['p_count']} <p> tags")
                            self._log(f"       Preview: {div['preview']}")
                
                break
        
        if not template_matched:
            self._log(f"  → No matching template found for this URL")
        
        # Phase 1.2: 噪音過濾（僅在模板失敗時執行）
        self._log("\n[Phase 1.2] Noise Filtering (ads, recommendations, related articles)")
        self._log("  → This phase runs because template matching did not succeed")
        
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
                
                # ⭐⭐⭐ 第一優先級：保護模板元素（在所有判斷之前）
                if id(el) in template_elements_to_protect:
                    protected_count += 1
                    continue
                
                # ⭐⭐⭐ 第二優先級：保護頂層容器元素（在所有判斷之前）
                tag_name = el.name.lower() if hasattr(el, 'name') else ''
                if tag_name in ['body', 'html', 'main']:
                    skipped_top_level += 1
                    continue
                
                # ⭐⭐⭐ 第三優先級：只過濾深度 >= 4 的元素（在所有判斷之前）
                depth = self._get_element_depth(el)
                if depth < 4:
                    skipped_shallow += 1
                    continue
                
                # === 以下才是實際的過濾邏輯 ===
                
                classes = el.get('class', [])
                classes_str = ' '.join(str(c) for c in classes).lower()
                id_str = str(el.get('id', '')).lower()
                
                # 檢查 class/id 是否包含噪音關鍵字
                if noisy_patterns.search(classes_str) or noisy_patterns.search(id_str):
                    p_count = len(el.find_all('p'))
                    text_len = len(el.get_text(strip=True))
                    
                    # OR 邏輯：段落少於5個 OR 文字少於800字
                    if p_count < 5 or text_len < 800:
                        elements_to_remove.append((el, f"noise_keyword (p={p_count}, len={text_len}, depth={depth})"))
                        continue
                
                # 改進的內容檢測
                text = el.get_text(" ", strip=True)
                if len(text) > 300:
                    # 檢查是否有很多直接子鏈接（列表特徵）
                    direct_links = el.find_all('a', recursive=False)
                    if len(direct_links) > 5:
                        elements_to_remove.append((el, f"many_links ({len(direct_links)} direct links, depth={depth})"))
                        continue
                    
                    # ⭐ 關鍵修正：計算標籤密度（相對值），而非絕對數量
                    category_tags = text.upper().count('ENTERTAINMENT') + \
                                  text.upper().count('BEAUTY') + \
                                  text.upper().count('FASHION') + \
                                  text.upper().count('LIFESTYLE')
                    
                    # 標籤密度 = 標籤數量 / (文本長度/100)
                    tag_density = category_tags / max(len(text) / 100, 1)
                    
                    # 只有當標籤密度 > 1.0 時才判定為列表（表示每100字超過1個標籤）
                    if tag_density > 1.0:
                        elements_to_remove.append((el, f"high_tag_density (density={tag_density:.2f}, tags={category_tags}, len={len(text)}, depth={depth})"))
                        
            except:
                continue
        
        self._log(f"  → Protected elements (from template): {protected_count}")
        self._log(f"  → Skipped top-level elements (body/html/main): {skipped_top_level}")
        self._log(f"  → Skipped shallow elements (depth < 4): {skipped_shallow}")
        self._log(f"  → Elements marked for removal: {len(elements_to_remove)}")
        
        if elements_to_remove:
            self._log(f"\n  [Removal Details]:")
            for i, (el, reason) in enumerate(elements_to_remove[:5], 1):
                classes = el.get('class', [])
                el_id = el.get('id', '')
                tag = el.name if hasattr(el, 'name') else 'unknown'
                self._log(f"    {i}. Tag: <{tag}>, Reason: {reason}")
                self._log(f"       Classes: {classes}")
                self._log(f"       ID: {el_id}")
            if len(elements_to_remove) > 5:
                self._log(f"    ... and {len(elements_to_remove) - 5} more")
        
        for el, reason in elements_to_remove:
            try:
                if el and hasattr(el, 'decompose'): 
                    el.decompose()
            except:
                pass
        
        # 移除 Fides 殘留
        try:
            fides_remnant = soup.find(id="fides-iframe-append")
            if fides_remnant:
                self._log(f"  → Removing Fides iframe remnant")
                fides_remnant.decompose()
        except:
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
        
        # 從模板選擇器添加
        if template_matched:
            tmpl = SITE_TEMPLATES[template_matched]
            for sel in tmpl['selectors']:
                _add_candidate(soup.select(sel), f"Template '{template_matched}': {sel}")
        
        # 從通用選擇器添加
        for sel in MAIN_CONTENT_SELECTORS:
            _add_candidate(soup.select(sel), f"General: {sel}")
        
        # 從啟發式規則添加
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
            if len(body_text) > 0:
                self._log(f"\n[Content Preview (first 300 chars)]:")
                self._log(f"{body_text[:300]}...")
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
        
        self._log(f"  → Candidates that passed filtering: {len(scored_candidates)}")
        self._log(f"  → Candidates filtered out as listing blocks: {len(filtered_out)}")
        
        if not scored_candidates:
            self._log("\n[WARNING] All candidates were filtered out!")
            self._log("  → This might indicate overly aggressive filtering")
            self._log("  → Falling back to full body text")
            body_text = self._clean_text(soup.get_text("\n", strip=True))
            self._log(f"  → Body text length: {len(body_text)} chars")
            if len(body_text) > 0:
                self._log(f"\n[Content Preview (first 300 chars)]:")
                self._log(f"{body_text[:300]}...")
            return body_text
        
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        
        self._log("\n  [Top 5 Candidates by Score]:")
        for i, (node, score, details) in enumerate(scored_candidates[:5], 1):
            path = self._css_path(node)
            text_len = len(node.get_text(strip=True))
            self._log(f"    {i}. Score: {score:.1f} | Length: {text_len} chars")
            self._log(f"       Path: {path[:100]}...")
            self._log(f"       Score breakdown: {details}")
        
        # Phase 4: 置信度計算
        best_node, best_score, _ = scored_candidates[0]
        second_score = scored_candidates[1][1] if len(scored_candidates) > 1 else 0.0
        confidence = self._calculate_confidence(best_score, second_score, best_node)
        
        self._log(f"\n[Phase 4] Confidence Calculation")
        self._log(f"  → Best score: {best_score:.1f}")
        self._log(f"  → Second score: {second_score:.1f}")
        self._log(f"  → Confidence: {confidence:.2%}")
        self._log(f"  → Threshold: {HEURISTIC_CONF_THRESHOLD:.2%}")
        
        # Phase 5: LLM 輔助（如果需要）
        if confidence < HEURISTIC_CONF_THRESHOLD and HAS_GENAI and self.genai_api_key:
            self._log(f"\n[Phase 5] Low Confidence - Requesting Gemini Assistance")
            selectors = self._ask_gemini_selector(url, soup)
            
            if selectors:
                self._log(f"  → Gemini suggested {len(selectors)} selectors")
                best_llm_text, best_llm_score, best_llm_selector = None, 0.0, None
                
                for sel in selectors:
                    try:
                        node = soup.select_one(sel)
                        if not node:
                            self._log(f"  → Selector '{sel}' did not match")
                            continue
                        
                        if self._looks_like_listing_block(node):
                            self._log(f"  → Selector '{sel}' is a listing block")
                            continue
                        
                        raw = node.get_text("\n", strip=True)
                        cleaned = self._clean_text(raw)
                        
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
                    self._log(f"\n  → ✅ Using Gemini's choice (score {best_llm_score:.1f} > {best_score:.1f})")
                    self._log(f"  → Selected selector: '{best_llm_selector}'")
                    if domain:
                        self.domain_selector_cache[domain] = best_llm_selector
                    
                    self._log(f"\n[Content Preview (first 300 chars)]:")
                    self._log(f"{best_llm_text[:300]}...")
                    self._log("\n" + "=" * 80)
                    self._log("[EXTRACTION COMPLETE - Using Gemini Selector]")
                    self._log("=" * 80)
                    return best_llm_text
                else:
                    self._log(f"  → Gemini's suggestions did not improve the result")
            else:
                self._log(f"  → Gemini did not provide usable selectors")
        
        # 返回最佳啟發式結果
        final_content = self._clean_text(best_node.get_text("\n", strip=True))
        best_path = self._css_path(best_node)
        
        self._log(f"\n[Final Selection]")
        self._log(f"  → Using heuristic choice")
        self._log(f"  → Selector path: {best_path[:150]}...")
        self._log(f"  → Score: {best_score:.1f}")
        self._log(f"  → Content length: {len(final_content)} chars")
        
        self._log(f"\n[Content Preview (first 300 chars)]:")
        self._log(f"{final_content[:300]}...")
        
        self._log("\n" + "=" * 80)
        self._log("[EXTRACTION COMPLETE - Using Heuristic Selection]")
        self._log("=" * 80 + "\n")
        
        return final_content

    def scrape(self, url: str) -> Dict[str, Any]:
        self._log(f"====== Starting scrape for: {url} ======")
        if self.driver is None:
            self._init_driver()
        
        debug_html_saved = False
        
        try:
            self.driver.get(url)
            self._apply_locale_spoofing_js()
            self._wait_for_content_load()
            
            # 先處理 Fides Cookie（Marie Claire 等網站）
            self._handle_fides_cookie()
            
            # 然後處理其他遮罩
            self._clear_overlays_and_click_cta()
            
            initial_source = self.driver.page_source
            initial_soup = BeautifulSoup(initial_source, 'html.parser')
            if self._is_listing_page(initial_soup):
                self._log(f"[Execution Strategy] Detected a listing page. Stopping processing for this URL to avoid incorrect extraction.")
                return {"status": "skipped", "url": url, "error": "Skipped: URL is an article list/category page."}
            self._log("[Execution Strategy] Detected a single article page. Proceeding with full scroll to load all content.")
            self._scroll_and_wait_for_full_load()
            
            # 特別處理 marieclaire
            if 'marieclaire.com' in url.lower():
                self._wait_for_marieclaire_content()
            
            # 檢查是否被重定向
            final_url = self.driver.current_url
            if final_url != url:
                self._log(f"[WARNING] URL changed!")
                self._log(f"  Original: {url}")
                self._log(f"  Final: {final_url}")
                
                # 檢查是否被重定向到分類頁面
                if '/entertainment' in final_url or '/beauty' in final_url or '/fashion' in final_url:
                    if '/entertainment' in final_url and '/entertainment/' not in url:
                        self._log(f"[ERROR] Redirected to category page! Article may be blocked by anti-bot.")
                        self._log(f"[SOLUTION] Try accessing the article directly in a browser to verify it exists.")
            
            final_source = self.driver.page_source
            title = self.driver.title or "No Title"
            self._log(f"[Extraction] Page fully loaded. Title: '{title}'. Starting main content analysis.")
            
            # 診斷模式：保存 HTML 供分析
            try:
                url_parts = url.split('/')
                article_id = url_parts[-1] if url_parts else 'unknown'
                debug_html_path = f'/tmp/marieclaire_{article_id}.html'
                with open(debug_html_path, 'w', encoding='utf-8') as f:
                    f.write(final_source)
                self._log(f"[Diagnostic] HTML saved to: {debug_html_path}")
                self._log(f"[Diagnostic] HTML length: {len(final_source):,} chars")
                
                # 分段輸出 HTML 供分析（前 10000 字符）
                self._log(f"\n[Diagnostic] HTML Content (first 10000 chars for analysis):")
                self._log("=" * 80)
                self._log("[HTML_START]")
                
                # 分成多段輸出，避免單行過長
                chunk_size = 1000
                html_to_output = final_source[:10000]
                for i in range(0, len(html_to_output), chunk_size):
                    chunk = html_to_output[i:i+chunk_size]
                    self._log(chunk)
                
                self._log("[HTML_END]")
                self._log("=" * 80)
                self._log(f"\n[Diagnostic] Total HTML length: {len(final_source):,} chars")
                self._log(f"[Diagnostic] Shown: {min(10000, len(final_source)):,} chars")
                
                debug_html_saved = True
            except Exception as e:
                self._log(f"[Diagnostic] Failed to save/output HTML: {e}")
            
            content = self._extract_main_text(final_source, url)
            if not content:
                error_msg = "Extracted content is empty after full analysis."
                if debug_html_saved:
                    url_parts = url.split('/')
                    article_id = url_parts[-1] if url_parts else 'unknown'
                    error_msg += f" HTML saved for diagnosis: /tmp/marieclaire_{article_id}.html"
                return {"status": "failed", "url": url, "error": error_msg}
            return {"status": "success", "url": url, "title": title, "content": content, "length": len(content)}
        except Exception as e:
            self._log(f"[Crawler] CRITICAL ERROR during scrape for {url}: {e}")
            traceback.print_exc()
            return {"status": "failed", "url": url, "error": str(e)}
        finally:
            self._log(f"====== Finished scrape for: {url} ======")
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