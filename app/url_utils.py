# -*- coding: utf-8 -*-
"""URL 工具（自 project_routes.py 抽出）。

URL 正規化去重鍵 + 容錯網址清單解析。純 urllib/re，無 db 依賴，可單獨 import 與測試。
project_routes / datasets_store 由此 import；外部仍可從 project_routes re-export 取得。
"""
import re

# 已知追蹤參數（保守清單）：去重時剝除，避免同頁因 utm/fbclid 等被當不同 URL 重複爬取。
_TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'utm_id',
    'utm_name', 'utm_reader', 'fbclid', 'gclid', 'gclsrc', 'dclid', 'msclkid',
    'mc_cid', 'mc_eid', 'igshid', 'ref_src', 'yclid', 'spm', '_ga',
}


def _url_key(url: str) -> str:
    """去重鍵：正規化 URL 以判同（保守，避免誤併不同頁）。
    小寫 scheme+host、去預設 port、去 fragment、去尾斜線、剝已知追蹤參數（保留其他 query）。
    原始 URL 仍另存供爬取/顯示，本函式只產生比對用的 key。"""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    u = (url or '').strip()
    if not u:
        return ''
    try:
        sp = urlsplit(u)
        scheme = (sp.scheme or '').lower()
        host = (sp.hostname or '').lower()
        if not host:
            return u.lower()
        netloc = host
        port = sp.port
        if port and not ((scheme == 'http' and port == 80) or (scheme == 'https' and port == 443)):
            netloc = f"{host}:{port}"
        path = sp.path or '/'
        if len(path) > 1 and path.endswith('/'):
            path = path.rstrip('/')
        q = [(k, v) for k, v in parse_qsl(sp.query, keep_blank_values=True)
             if k.lower() not in _TRACKING_PARAMS]
        q.sort()
        return urlunsplit((scheme, netloc, path, urlencode(q), ''))  # 去 fragment
    except Exception:
        return u.lower()


def parse_url_list(raw: str) -> list:
    """容錯解析網址清單，回傳去重保序的 http(s) 網址。

    處理：真換行、被 URL 編碼的換行/空白（%0A/%0D/%20）、空白分隔、
    以及多個網址黏成一坨（用 lookahead 在每個 http(s):// 前切開）。
    去重以 _url_key 正規化判同（同頁不同追蹤參數/尾斜線/fragment 視為同一）。
    """
    if not raw:
        return []
    raw = (raw.replace('%0D', '\n').replace('%0d', '\n')
              .replace('%0A', '\n').replace('%0a', '\n')
              .replace('%20', ' ').replace('%09', ' '))
    seen, out = set(), []
    for tok in re.split(r'\s+', raw.strip()):
        for part in re.split(r'(?=https?://)', tok):
            p = part.strip().strip('<>"\'，。、')
            if p.startswith(('http://', 'https://')):
                k = _url_key(p)
                if k and k not in seen:
                    seen.add(k)
                    out.append(p)
    return out
