# -*- coding: utf-8 -*-
"""
分層爬取 fallback（Tier 2 / Tier 3）

設計原則（見 CRAWLER_STRATEGY.md 第 4 節）：
  - Tier 1：undetected-chromedriver（crawler.py 既有，零額外成本）
  - Tier 2：Gemini URL 直讀（本模組，僅 token 成本）
  - Tier 3：Webshare 住宅 IP 代理（本模組，付費，僅對失敗網址啟用）

⚠️ 全部由環境變數控制，預設關閉。未設定憑證時所有函式都是 no-op，
   不影響現有 Tier 1 流程。等使用者填入 Webshare 憑證後再啟用。

環境變數：
  ENABLE_GEMINI_URL_FALLBACK = "1"      啟用 Tier 2
  GENAI_API_KEY                          Tier 2 用（與既有共用）

  WEBSHARE_PROXY_ENABLED     = "1"      啟用 Tier 3
  WEBSHARE_PROXY_HOST                    例：proxy.webshare.io
  WEBSHARE_PROXY_PORT                    例：80
  WEBSHARE_PROXY_USER                    Webshare 帳號
  WEBSHARE_PROXY_PASS                    Webshare 密碼
"""
import os
import json
import tempfile
from typing import Optional, Dict


# ──────────────────────────────────────────────────────────────────────
# Tier 3：Webshare 住宅 IP 代理
# ──────────────────────────────────────────────────────────────────────

def load_proxy_config() -> Optional[Dict[str, str]]:
    """從環境變數載入 Webshare 代理設定。

    回傳 None 表示未啟用（預設）；否則回傳 {host, port, user, pass}。
    只有 WEBSHARE_PROXY_ENABLED == "1" 且 host/port 齊全才回傳設定。
    """
    if os.environ.get("WEBSHARE_PROXY_ENABLED", "") != "1":
        return None
    host = os.environ.get("WEBSHARE_PROXY_HOST", "").strip()
    port = os.environ.get("WEBSHARE_PROXY_PORT", "").strip()
    if not host or not port:
        return None
    return {
        "host": host,
        "port": port,
        "user": os.environ.get("WEBSHARE_PROXY_USER", "").strip(),
        "pass": os.environ.get("WEBSHARE_PROXY_PASS", "").strip(),
    }


def build_proxy_auth_extension(proxy: Dict[str, str]) -> Optional[str]:
    """建立一個臨時 Chrome 擴充，處理需要帳密驗證的 HTTP proxy。

    Chrome 的 --proxy-server 不支援在 URL 內帶帳密；標準做法是用一個
    background script 攔截 onAuthRequired 事件自動回填憑證。

    回傳擴充資料夾路徑（呼叫端以 --load-extension 載入），失敗回傳 None。
    無帳密時回傳 None（呼叫端改用單純的 --proxy-server）。
    """
    if not proxy.get("user") or not proxy.get("pass"):
        return None

    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Webshare Proxy Auth",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "76.0.0",
    }

    background_js = """
var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: {
            scheme: "http",
            host: "%(host)s",
            port: parseInt("%(port)s")
        },
        bypassList: ["localhost"]
    }
};
chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
function callbackFn(details) {
    return {
        authCredentials: {
            username: "%(user)s",
            password: "%(pass)s"
        }
    };
}
chrome.webRequest.onAuthRequired.addListener(
    callbackFn,
    {urls: ["<all_urls>"]},
    ["blocking"]
);
""" % {
        "host": proxy["host"],
        "port": proxy["port"],
        "user": proxy["user"],
        "pass": proxy["pass"],
    }

    ext_dir = tempfile.mkdtemp(prefix="webshare_proxy_ext_")
    with open(os.path.join(ext_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f)
    with open(os.path.join(ext_dir, "background.js"), "w") as f:
        f.write(background_js)
    return ext_dir


def apply_proxy_to_options(options, proxy: Dict[str, str], log_fn=None) -> Optional[str]:
    """把 proxy 套用到 ChromeOptions。

    - 有帳密：建立 proxy auth 擴充並以 --load-extension 載入，回傳擴充路徑。
    - 無帳密：直接用 --proxy-server，回傳 None。

    回傳的擴充路徑供呼叫端在 driver 結束後清理（可不清理，temp 會被系統回收）。
    """
    def _log(m):
        if log_fn:
            log_fn(m)

    if proxy.get("user") and proxy.get("pass"):
        ext_dir = build_proxy_auth_extension(proxy)
        if ext_dir:
            options.add_argument(f"--load-extension={ext_dir}")
            _log(f"[Tier3] Webshare proxy（含驗證）已掛載：{proxy['host']}:{proxy['port']}")
            return ext_dir
    # 無帳密 fallback
    options.add_argument(f"--proxy-server=http://{proxy['host']}:{proxy['port']}")
    _log(f"[Tier3] Webshare proxy（無驗證）已掛載：{proxy['host']}:{proxy['port']}")
    return None


# ──────────────────────────────────────────────────────────────────────
# Tier 2：Gemini URL 直讀
# ──────────────────────────────────────────────────────────────────────

def is_gemini_url_fallback_enabled() -> bool:
    return os.environ.get("ENABLE_GEMINI_URL_FALLBACK", "") == "1"


# 視為「需要升級到下一層」的條件：失敗，或成功但內文過短（疑似只抓到導語）
TIER_UPGRADE_MIN_LEN = 200


def needs_upgrade(result: dict, min_len: int = TIER_UPGRADE_MIN_LEN) -> bool:
    if not result:
        return True
    if result.get("status") == "skipped":
        return False  # skip（需登入等）升級也沒用
    if result.get("status") != "success":
        return True
    return len(result.get("content") or "") < min_len


def run_tier23(url: str, tier1_result: dict, gemini_api_key: str,
               proxied_scrape_fn=None, log_fn=None) -> dict:
    """分層協調（Tier 2 → 3）。輸入 Tier 1 結果，需要時依序升級。

    - Tier 2：Gemini URL 直讀（env ENABLE_GEMINI_URL_FALLBACK + 有 key）。
    - Tier 3：呼叫 proxied_scrape_fn(url)（由呼叫端提供，內部用 use_proxy=True 的 crawler）。

    Tier 2/3 皆 env 控制、預設關閉：未設定時直接回傳 Tier 1 結果，行為不變。
    """
    if not needs_upgrade(tier1_result):
        return tier1_result

    # ── Tier 2 ──
    try:
        if is_gemini_url_fallback_enabled() and gemini_api_key:
            text = gemini_url_read(url, gemini_api_key, log_fn=log_fn)
            if len(text) >= TIER_UPGRADE_MIN_LEN:
                return {"status": "success", "url": url,
                        "title": (tier1_result or {}).get("title") or "(Tier2 Gemini)",
                        "content": text, "length": len(text), "tier": 2}
    except Exception as e:
        if log_fn:
            log_fn(f"[Tier2] 協調失敗：{e}")

    # ── Tier 3 ──
    try:
        if load_proxy_config() is not None and proxied_scrape_fn is not None:
            if log_fn:
                log_fn(f"[Tier3] Tier1/2 未達標，改用 Webshare 代理重試：{url}")
            proxied = proxied_scrape_fn(url)
            if not needs_upgrade(proxied):
                proxied["tier"] = 3
                return proxied
    except Exception as e:
        if log_fn:
            log_fn(f"[Tier3] 協調失敗：{e}")

    return tier1_result


def gemini_url_read(url: str, api_key: str, log_fn=None) -> str:
    """Tier 2：把 URL 交給 Gemini，請其回傳該頁面的正文純文字。

    使用 google-genai 的 url_context 工具（讓模型自行抓取 URL 內容）。
    僅在 ENABLE_GEMINI_URL_FALLBACK=1 且有 api_key 時由呼叫端啟用。

    回傳萃取到的正文；失敗回傳空字串。
    """
    def _log(m):
        if log_fn:
            log_fn(m)

    if not api_key:
        return ""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        _log("[Tier2] google-genai 未安裝，略過")
        return ""

    try:
        client = genai.Client(api_key=api_key)
        prompt = (
            f"請讀取這個網址的文章內容並回傳「純文字正文」，"
            f"只要文章主體段落，不要導覽列、廣告、相關文章、留言或版權宣告。"
            f"用原文語言輸出，不要加任何說明或標題。網址：{url}"
        )
        # url_context 工具讓模型自行抓取 URL（Gemini 2.x 支援）
        config = types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext())],
            temperature=0.1,
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=config,
        )
        text = (getattr(resp, "text", None) or "").strip()
        _log(f"[Tier2] Gemini URL 直讀回傳 {len(text)} 字")
        return text
    except Exception as e:
        _log(f"[Tier2] Gemini URL 直讀失敗：{e}")
        return ""
