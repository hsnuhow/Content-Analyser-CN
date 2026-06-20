# -*- coding: utf-8 -*-
"""SSRF 守門與安全 HTTP 抓取（net_guard）。

集中管理 SSRF 防護：
  - is_safe_url(url) -> (ok, reason)：對入口 URL 做協議/host/IP 判定（含 DNS 實解析）。
  - safe_urlopen(url, timeout, max_bytes=None, max_redirects=5)：在「每一跳」都重新
    驗 SSRF 的安全版 urlopen。標準 urllib 會自動跟隨 302，原本只驗入口 URL 的設計
    可被「入口公網 → 302 導向 169.254.169.254」繞過讀取 GCP metadata；本函式手動
    逐跳處理 redirect，每跳 Location 都先 is_safe_url 才續跳。

本模組刻意不 import app/crawler，避免循環依賴。
"""
import ipaddress
import socket
import urllib.request
import urllib.error
from urllib.parse import urlparse, urljoin


def _v4_dangerous(v4: "ipaddress.IPv4Address") -> bool:
    """IPv4 是否落入危險範圍（私有/loopback/link-local/reserved/multicast/unspecified）。
    Cloud Run 出口實走 IPv4，故對 IPv4 採嚴格判定。"""
    return bool(v4.is_private or v4.is_loopback or v4.is_link_local
                or v4.is_reserved or v4.is_multicast or v4.is_unspecified)


def _v4_internal(v4: "ipaddress.IPv4Address") -> bool:
    """IPv4 是否為「真正可被 SSRF 利用的內網位址」（RFC1918/loopback/link-local/reserved），
    但排除 0.0.0.0 之類 unspecified（畸形 6to4 解出的垃圾值，非可路由的內網目標）。
    供 IPv6 內嵌 v4 的縱深判定使用。"""
    return bool((v4.is_private or v4.is_loopback or v4.is_link_local or v4.is_reserved)
                and not v4.is_unspecified)


def _ipv6_embedded_v4(ip6: "ipaddress.IPv6Address"):
    """取 IPv6 內嵌的 IPv4：v4-mapped(::ffff:0:0/96)、6to4(2002::/16)、Teredo(2001:0::/32)。
    無內嵌 v4 則回 None。用於擋「6to4 包私有 v4（如 2002:a9fe:a9fe:: → 169.254.169.254）」
    這類繞過。"""
    try:
        b = ip6.packed
        if b[:10] == b"\x00" * 10 and b[10:12] == b"\xff\xff":   # v4-mapped ::ffff:0:0/96
            return ipaddress.IPv4Address(bytes(b[12:16]))
        if b[0] == 0x20 and b[1] == 0x02:                          # 6to4 2002::/16 → bytes 2..5
            return ipaddress.IPv4Address(bytes(b[2:6]))
        if b[0] == 0x20 and b[1] == 0x01 and b[2] == 0x00 and b[3] == 0x00:  # Teredo → 末 4 byte XOR 0xff
            return ipaddress.IPv4Address(bytes(x ^ 0xFF for x in b[12:16]))
    except Exception:
        return None
    return None


def _v6_internal(ip6: "ipaddress.IPv6Address") -> bool:
    """IPv6 是否指向真正內網（loopback/link-local/multicast/ULA(is_private)，
    或 6to4/Teredo/v4-mapped 內嵌的私有 v4）。

    刻意**不**用 is_reserved 過度封鎖：Python 3.11 對 6to4(2002::/16) 全域位址會誤判
    is_reserved=True，導致「有合法公網 A 記錄、卻附帶一個 6to4 AAAA」的網站
    （如 www.100.com.tw → 2002::cb45:4206）整站被自家 SSRF 守門誤殺。
    真正的內網 IPv6（::1 / fe80:: / fc00:: / 6to4 包私有 v4）仍會被擋。"""
    if ip6.is_loopback or ip6.is_link_local or ip6.is_multicast or ip6.is_private:
        return True
    emb = _ipv6_embedded_v4(ip6)
    if emb is not None and _v4_internal(emb):
        return True
    return False


def is_safe_url(url: str):
    """C1 SSRF 防護：阻擋私有/保留 IP、loopback、link-local（含 GCP metadata）。

    關鍵：對 domain name **實際解析 DNS**，落入危險範圍即拒，
    防止「域名 A 記錄指向 169.254.169.254 / 內網」這類繞過（先前直接信任 DNS）。

    判定原則（修正 6to4 AAAA 誤殺合法站台）：
      - IPv4 是 Cloud Run 實際出口 → 嚴格，任一危險 v4 即拒。
      - IPv6 縱深防禦 → 僅當指向真正內網才拒（不因 is_reserved 過度封鎖全域/6to4 位址）；
        6to4/Teredo 仍會解出內嵌 v4 檢查，擋住包私有位址的繞過。
    回傳 (ok: bool, reason: str)。
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"非 http/https 協議：{parsed.scheme}"
        host = parsed.hostname or ""
        if not host:
            return False, "缺少 hostname"
        _BLOCKED_HOSTS = {"metadata.google.internal", "169.254.169.254"}
        if host.lower() in _BLOCKED_HOSTS:
            return False, f"禁止存取 metadata endpoint：{host}"
        # 收集 host 對應的所有 IP（IP 字面值直接用；否則解析 DNS）
        candidates = []
        try:
            candidates = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, None)
                candidates = [ipaddress.ip_address(i[4][0]) for i in infos]
            except Exception as e:
                return False, f"無法解析 hostname：{host}（{e}）"
        if not candidates:
            return False, f"無法解析 hostname：{host}"
        for ip in candidates:
            if ip.version == 4 and _v4_dangerous(ip):
                return False, f"禁止存取保留/私有 IP：{host} → {ip}"
            if ip.version == 6 and _v6_internal(ip):
                return False, f"禁止存取保留/私有 IPv6：{host} → {ip}"
        return True, ""
    except Exception as e:
        return False, str(e)


# 底線版別名（相容既有呼叫點：app.py 等以 _is_safe_url 命名匯入）。
_is_safe_url = is_safe_url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自動跟隨 redirect 的 handler：把 redirect 當成可讀的 response 交回，
    由 safe_urlopen 逐跳取 Location 重新做 SSRF 判定後再續跳。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 只裝「不自動 redirect」的 opener；其餘行為（http/https/cookie 無）採預設。
_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


class _SafeResponse:
    """safe_urlopen 的回傳物件：包住底層 http.client.HTTPResponse，
    提供 .read(n) / .headers / context-manager，行為對齊 urlopen 回傳。
    若建構時帶入 _prefetched（已預讀 max_bytes 的 bytes），.read() 直接回該緩衝。"""

    def __init__(self, raw, prefetched=None):
        self._raw = raw
        self._prefetched = prefetched
        self.headers = getattr(raw, "headers", None)
        self.status = getattr(raw, "status", None)
        self.url = getattr(raw, "url", None)

    def read(self, *args, **kwargs):
        if self._prefetched is not None:
            data = self._prefetched
            self._prefetched = None
            return data
        return self._raw.read(*args, **kwargs)

    def getheader(self, name, default=None):
        try:
            return self._raw.getheader(name, default)
        except Exception:
            if self.headers is not None:
                return self.headers.get(name, default)
            return default

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def safe_urlopen(url, timeout=15, max_bytes=None, max_redirects=5):
    """SSRF 安全版 urlopen：每一跳（含 redirect 目標）都先過 is_safe_url 才連線。

    參數：
      url          : URL 字串，或 urllib.request.Request（保留自訂 headers/UA）。
      timeout      : 每跳逾時秒數。
      max_bytes    : 若給定，預讀此上限 bytes 並讓回傳物件 .read() 直接回該緩衝
                     （避免無上限讀取）。None 則回傳物件照常串流，由呼叫端自控。
      max_redirects: 最多跟隨幾次 redirect，超過拋 urllib.error.URLError。

    回傳：_SafeResponse（支援 with、.read(n)、.headers、.getheader）。
    不安全的入口或 redirect 目標 → 拋 urllib.error.URLError（reason 含被擋原因）。
    """
    # 接受 Request（保留 headers）或字串；統一取出 method/headers/data。
    if isinstance(url, urllib.request.Request):
        req = url
        current_url = req.full_url
        base_headers = dict(req.header_items())
        data = req.data
        method = req.get_method()
    else:
        current_url = url
        base_headers = {}
        data = None
        method = "GET"

    redirects = 0
    while True:
        ok, reason = is_safe_url(current_url)
        if not ok:
            raise urllib.error.URLError(f"SSRF 守門擋下：{reason}")
        hop = urllib.request.Request(current_url, data=data, method=method)
        for k, v in base_headers.items():
            hop.add_header(k, v)
        # _NoRedirect 讓 redirect_request 回 None；但預設 HTTPErrorProcessor 仍會把 3xx
        # 當錯誤拋 HTTPError。HTTPError 本身是可讀的 response（含 .headers/.read），
        # 故對 3xx 攔下、取 Location 逐跳續走；非 3xx 的 HTTPError（4xx/5xx）照常往外拋。
        try:
            raw = _NO_REDIRECT_OPENER.open(hop, timeout=timeout)
            code = getattr(raw, "status", None)
        except urllib.error.HTTPError as he:
            if he.code in (301, 302, 303, 307, 308):
                raw = he
                code = he.code
            else:
                raise
        if code in (301, 302, 303, 307, 308):
            loc = raw.headers.get("Location") if raw.headers else None
            try:
                raw.close()
            except Exception:
                pass
            if not loc:
                raise urllib.error.URLError("redirect 缺少 Location")
            redirects += 1
            if redirects > max_redirects:
                raise urllib.error.URLError(f"redirect 次數超過上限（{max_redirects}）")
            current_url = urljoin(current_url, loc)
            # 303 與部分 302 慣例改用 GET 且去掉 body。
            if code == 303 or (code == 302 and method != "GET" and method != "HEAD"):
                method = "GET"
                data = None
            continue
        # 非 redirect：成功回傳。
        if max_bytes is not None:
            prefetched = raw.read(max_bytes)
            return _SafeResponse(raw, prefetched=prefetched)
        return _SafeResponse(raw)
