# -*- coding: utf-8 -*-
"""頁面分類啟發式（純函式，無 driver / 無 I/O）：判斷抽取到的內容是否為
瀏覽器連線錯誤頁 / HTTP 錯誤頁 / 反爬封鎖頁，而非真正文章。

從 crawler.HeadlessCrawler 抽出（原 _looks_like_*_page 方法）：純字串判定、可單元測試，
與 driver 編排邏輯分離。crawler.py 以 from page_classify import ... 取用。
"""

# 瀏覽器錯誤頁 / 反爬蟲挑戰頁（Cloudflare 等）的特徵字串。命中代表抓到的不是真正內容
# （站台連不上，或被反爬蟲攔下顯示驗證頁），應視為失敗，讓分層 fallback（Tier 3 代理）接手。
BROWSER_ERROR_MARKERS = (
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

_HTTP_ERROR_MARKERS = (
    "403 forbidden", "404 not found", "error 403", "error 404",
    "access denied", "forbidden", "not found", "503 service",
    "拒絕存取", "找不到網頁", "頁面不存在", "請求被拒絕", "禁止存取",
)

# 反爬封鎖/驗證頁特徵（這些字串幾乎只出現在封鎖頁，不會是正常文章短正文）
_BLOCK_PAGE_MARKERS = (
    "禁止爬取", "禁止爬蟲", "禁止抓取", "您的請求被拒絕", "請求被拒絕",
    "access denied", "請完成驗證", "驗證您是人類", "輸入驗證碼", "我們偵測到",
    "異常流量", "just a moment", "checking your browser",
    "enable javascript and cookies", "attention required",
)


def looks_like_browser_error_page(content: str, title: str = "") -> bool:
    """判斷抽取到的內容是否為瀏覽器連線錯誤頁（而非真正文章）。

    條件（保守，避免誤判真文章）：內容偏短（< 1500 字）且命中錯誤特徵字串。
    錯誤頁通常很短且 title 僅為網域名稱。
    """
    if not content:
        return False
    if len(content) >= 1500:
        return False  # 長內容幾乎不可能是錯誤頁
    hits = sum(1 for m in BROWSER_ERROR_MARKERS if m in content)
    # 命中 1 個強特徵即可（這些字串幾乎不會出現在正常文章正文）
    return hits >= 1


def looks_like_http_error_page(content: str, title: str = "") -> bool:
    """偵測 HTTP 錯誤頁（403/404/503 等）：站台回錯誤頁但被當成短內文。

    保守：僅在內容很短（< 150 字）時才判定，避免長文提到 forbidden/not found 被誤殺。
    """
    blob = f"{title}\n{content}".lower().strip()
    return any(m in blob for m in _HTTP_ERROR_MARKERS)


def looks_like_block_page(content: str, title: str = "") -> bool:
    """偵測反爬封鎖/驗證頁（站台對爬蟲回的「禁止爬取」/Cloudflare 挑戰/captcha）。
    保守：命中強特徵 **且** 內容偏短（< 1200 字）才判定，避免長文偶提關鍵字被誤殺。
    命中 → 上層標記『需手動爬取』，不把封鎖頁當文章污染分析。"""
    blob = f"{title}\n{content}".lower().strip()
    if not any(m in blob for m in _BLOCK_PAGE_MARKERS):
        return False
    return len((content or "").strip()) < 1200


def detect_paywall_incomplete(content: str, url: str = "",
                              markers=(), paywall_domains=None) -> tuple:
    """偵測「付費牆截斷」的不完整內容。回 (incomplete: bool, reason: str)。

    markers / paywall_domains 由呼叫端注入（crawler_config 的 floor + Firestore），本模組維持純函式。
    兩種付費牆型態（實測天下/商周/端傳媒歸納）：
      A) 內容含付費牆 CTA 標記（如天下「訂戶限定」「查看訂閱方案」「不限篇數暢讀」）
         → (True, 'paywall')。最可靠。
      B) 網域屬「已知靜默截斷付費牆站」（商周/端傳媒：付費牆是 JS 遮罩、抓不到 CTA 文字，
         只抓到引言）且抽到的內容短於該網域門檻 → (True, 'paywall_short')。啟發式。

    註：寧可「多標不完整」也不要漏（不完整內容會污染分析）；admin 可於後台調 markers/門檻。
    """
    c = content or ""
    for m in (markers or ()):
        if m and m in c:
            return True, "paywall"
    u = (url or "").lower()
    for dom, min_len in (paywall_domains or {}).items():
        try:
            if dom and dom in u and len(c) < int(min_len):
                return True, "paywall_short"
        except (TypeError, ValueError):
            continue
    return False, ""
