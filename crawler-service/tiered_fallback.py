# -*- coding: utf-8 -*-
"""
分層爬取 fallback（Tier 3）

設計原則（見 CRAWLER_STRATEGY.md 第 4 節）：
  - Tier 1：undetected-chromedriver（crawler.py 既有，零額外成本）
  - Tier 3：Webshare 住宅 IP 代理（本模組，付費，僅對失敗網址啟用）

⛔ Tier 2「Gemini URL 直讀」已於 2026-06 廢除：
   實測 Gemini 的 url_context 工具透過 API 連維基百科都讀不到（官方文件還推薦維基當測試），
   是該工具本身不穩（開發者社群普遍回報），對 Cloudflare 難站（如 Dcard）更無解。
   難站改走 Tier 3 住宅代理或 Cowork（真實瀏覽器 + 住宅 IP）。

⚠️ Tier 3 由環境變數控制，預設關閉。未設定憑證時為 no-op，不影響現有 Tier 1 流程。

環境變數：
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
# Tier 3：住宅/資料中心代理（provider-agnostic：Decodo / Webshare 等）
# ──────────────────────────────────────────────────────────────────────

# Firestore flag 快取（避免每次 scrape 都讀 Firestore）
_tier3_flag_cache = {"val": None, "ts": 0.0}


def _read_tier3_firestore_flag() -> Optional[bool]:
    """讀 Firestore `system/config.tier3_enabled`（admin 後台開關），60s 快取。

    回傳 True/False = 後台明確設定；None = 未設定（交給 env 決定）。
    讀取失敗（Firestore 不可用）回 None，不影響既有 env 行為。
    """
    import time as _time
    now = _time.time()
    if now - _tier3_flag_cache["ts"] < 60:
        return _tier3_flag_cache["val"]
    val: Optional[bool] = None
    try:
        from firebase_admin import firestore as _fs
        doc = _fs.client().collection("system").document("config").get()
        if doc.exists:
            v = doc.to_dict().get("tier3_enabled")
            if isinstance(v, bool):
                val = v
    except Exception:
        val = None
    _tier3_flag_cache["val"] = val
    _tier3_flag_cache["ts"] = now
    return val


def load_proxy_config() -> Optional[Dict[str, str]]:
    """載入 Tier 3 代理設定（provider-agnostic）。

    開關（on/off）優先序：Firestore `system/config.tier3_enabled`（admin 後台 toggle）
      > env `PROXY_ENABLED` / `WEBSHARE_PROXY_ENABLED`。憑證一律來自 env。
    回傳 None = 未啟用；否則 {host, port, user, pass, provider}。

    Decodo（residential）範例 env：
      PROXY_HOST=gate.decodo.com  PROXY_PORT=10001  PROXY_USER=...  PROXY_PASS=...
    （開關可由後台 toggle 控制，不必重建 revision。）
    """
    def _get(generic: str, legacy: str) -> str:
        return (os.environ.get(generic) or os.environ.get(legacy) or "").strip()

    env_enabled = _get("PROXY_ENABLED", "WEBSHARE_PROXY_ENABLED") == "1"
    flag = _read_tier3_firestore_flag()
    if flag is True:
        enabled = True
    elif flag is False:
        enabled = False
    else:
        enabled = env_enabled  # 後台未設定 → 用 env
    if not enabled:
        return None
    host = _get("PROXY_HOST", "WEBSHARE_PROXY_HOST")
    port = _get("PROXY_PORT", "WEBSHARE_PROXY_PORT")
    if not host or not port:
        return None
    return {
        "host": host,
        "port": port,
        "user": _get("PROXY_USER", "WEBSHARE_PROXY_USER"),
        "pass": _get("PROXY_PASS", "WEBSHARE_PROXY_PASS"),
        "provider": os.environ.get("PROXY_PROVIDER", "decodo" if os.environ.get("PROXY_HOST") else "webshare"),
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

    # ⭐ 值一律以 json.dumps 編碼後嵌入 JS，避免帳密含 " / \\ / </script> 等字元破壞腳本。
    background_js = """
var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: {
            scheme: "http",
            host: %(host)s,
            port: parseInt(%(port)s)
        },
        bypassList: ["localhost"]
    }
};
chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
function callbackFn(details) {
    return {
        authCredentials: {
            username: %(user)s,
            password: %(pass)s
        }
    };
}
chrome.webRequest.onAuthRequired.addListener(
    callbackFn,
    {urls: ["<all_urls>"]},
    ["blocking"]
);
""" % {
        "host": json.dumps(str(proxy["host"])),
        "port": json.dumps(str(proxy["port"])),
        "user": json.dumps(str(proxy["user"])),
        "pass": json.dumps(str(proxy["pass"])),
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
# Tier 3 升級協調 + 共用「需升級」判定
# （Tier 2「Gemini URL 直讀」已於 2026-06 廢除——見模組 docstring。）
# ──────────────────────────────────────────────────────────────────────


# 視為「需要升級到下一層」的條件：失敗，或成功但內文過短（疑似只抓到導語）
TIER_UPGRADE_MIN_LEN = 200


def needs_upgrade(result: dict, min_len: int = TIER_UPGRADE_MIN_LEN) -> bool:
    if not result:
        return True
    if result.get("cloaked"):
        return True  # cloaking/反爬封鎖 → 住宅代理重抓可能破解（Tier3 開啟時生效；關閉時保留 needs_manual 標記）
    if result.get("status") == "skipped":
        return False  # skip（需登入等）升級也沒用
    if result.get("status") != "success":
        return True
    return len(result.get("content") or "") < min_len


def run_tier3(url: str, tier1_result: dict,
              proxied_scrape_fn=None, log_fn=None) -> dict:
    """Tier 3 升級協調：輸入 Tier 1 結果，未達標（失敗/內文過短/cloaked）時用住宅代理重抓一次。

    呼叫 proxied_scrape_fn(url)（由呼叫端提供，內部用 use_proxy=True 的 crawler）。
    Tier 3 由 proxy 設定控制、預設關閉：未設定代理時直接回傳 Tier 1 結果，行為不變。
    （Tier 2「Gemini URL 直讀」已於 2026-06 廢除——見模組 docstring。）
    """
    if not needs_upgrade(tier1_result):
        return tier1_result

    # ── Tier 3：住宅代理重試 ──
    try:
        if load_proxy_config() is not None and proxied_scrape_fn is not None:
            if log_fn:
                log_fn(f"[Tier3] Tier1 未達標，改用 Webshare 代理重試：{url}")
            proxied = proxied_scrape_fn(url)
            if not needs_upgrade(proxied):
                proxied["tier"] = 3
                return proxied
    except Exception as e:
        if log_fn:
            log_fn(f"[Tier3] 協調失敗：{e}")

    return tier1_result


