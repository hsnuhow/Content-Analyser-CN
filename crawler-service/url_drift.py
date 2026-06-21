# -*- coding: utf-8 -*-
"""自動轉移偵測（純函式，無 driver / 無 I/O）：判斷渲染後最終 URL 是否被「自動轉移到非原文目標」
（cloaking 跨站 / 登入·同意牆 / 導回首頁 / 錯誤頁），用來不把誤導內容當原文。

比舊版（只比可註冊網域）更廣，但**保守設計**：避開合法 redirect（http→https、尾斜線、
m. 行動版、locale 前綴、tracking 去除）造成的誤判。crawler.py 委派至此。
"""
from urllib.parse import urlparse

# 同註冊域但屬「登入/同意牆」的子網域前綴 → 轉到這些代表離開原文。
_WALL_SUBS = {"auth", "login", "signin", "sso", "consent", "account", "passport", "id"}
# path 含這些片段 → 轉到登入/錯誤/封鎖頁。
_WALL_PATHS = ("/login", "/signin", "/consent", "/sso", "/error", "/blocked", "/verify", "/captcha")


def reg_host(h: str) -> str:
    """可註冊網域（末兩段），用於跨站比對。"""
    h = (h or "").lower().split(":")[0]
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def detect_auto_transfer(url: str, final_url: str):
    """渲染後最終 URL 是否被自動轉移到非原文目標。回 (transferred: bool, reason: str)。

    判定（由強到弱）：
      ① 跨可註冊網域 → cloaking / 完全轉站。
      ② 同註冊域但子網域變成 auth/login/consent… → 登入/同意牆。
      ③ 深層文章 path 被導回首頁（final path 變成 / 或空）→ 文章疑下架/被導離。
      ④ final path 含 /login、/consent、/error… → 登入/錯誤頁。
    刻意只判上述「明確離開原文」的情況，合法 redirect（http→https、尾斜線、m. 行動版、
    /en/ locale、去 tracking）host/path 實質不變，不會誤判。
    """
    try:
        ru, fu = urlparse(url), urlparse(final_url)
        if not fu.hostname:
            return False, ""
        rhost, fhost = (ru.hostname or "").lower(), (fu.hostname or "").lower()
        # ① 跨可註冊網域
        if reg_host(rhost) != reg_host(fhost):
            return True, f"內容飄移到不同網域（{fu.hostname}），疑似 cloaking 反爬"
        # ② 子網域變成登入/同意牆
        fsub, rsub = fhost.split(".")[0], rhost.split(".")[0]
        if fsub in _WALL_SUBS and fsub != rsub:
            return True, f"轉址到登入/同意子網域（{fu.hostname}），非原文"
        # ③ 深層 path 被導回首頁
        rpath, fpath = (ru.path or "/").rstrip("/"), (fu.path or "/").rstrip("/")
        if len(rpath) > 1 and fpath in ("", "/"):
            return True, "轉址到網站首頁（疑文章已下架/被導離，非原文）"
        # ④ 轉到登入/錯誤/封鎖 path
        low = fpath.lower()
        if any(k in low for k in _WALL_PATHS):
            return True, f"轉址到登入/錯誤頁（{fpath[:40]}），非原文"
        return False, ""
    except Exception:
        return False, ""
