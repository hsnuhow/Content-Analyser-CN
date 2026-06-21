# -*- coding: utf-8 -*-
"""HTML 解析/結構判定（純函式，無 driver / 無 I/O）：JSON-LD / RSC block payload 抽取、
meta 後備、列表頁判定、CMP 容器移除。從 crawler.HeadlessCrawler 抽出，邏輯逐字保留、可測。

依賴單向：crawler.py → dom_parse → text_clean（本模組不 import crawler）。log_fn 可選。
"""
import re
import json

from bs4 import BeautifulSoup

import text_clean

# OneTrust / Fides / 通用 CMP 同意視窗容器（抽取前移除，避免 cookie 說明被誤判主文）。
CMP_REMOVE_SELECTORS = [
    "#onetrust-consent-sdk", "#onetrust-banner-sdk", "#onetrust-pc-sdk",
    "#ot-sdk-container", "#ot-sdk-cookie-policy", ".onetrust-pc-dark-filter",
    "[id^='onetrust']", "[class*='onetrust']", "[class*='ot-sdk']",
    "[id*='fides']", "[class*='fides']",
    "[id*='cookie-consent']", "[class*='cookie-consent']",
]


def apply_meta_fallback(content: str, html: str, log_fn=None) -> str:
    """主文過短（< 200 字）時，補入 og:description / meta description 作為導語。"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        meta_desc = None
        ogd = soup.find('meta', attrs={'property': 'og:description'})
        if ogd and ogd.get('content'):
            meta_desc = ogd['content'].strip()
        if not meta_desc:
            m = soup.find('meta', attrs={'name': 'description'})
            if m and m.get('content'):
                meta_desc = m['content'].strip()
        if meta_desc and meta_desc not in content:
            if log_fn:
                log_fn(f"[Fallback] 主文過短（{len(content)} 字），補入 meta description")
            return meta_desc + "\n\n" + content
    except Exception:
        pass
    return content


def extract_from_json_ld(html: str, log_fn=None) -> str:
    """從 JSON-LD <script> 中萃取 articleBody 文字（MirrorMedia 等 Next.js 站）。"""
    try:
        ld_scripts = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL | re.I
        )
        for raw_json in ld_scripts:
            try:
                data = json.loads(raw_json.strip())
            except Exception:
                continue
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
                    if log_fn:
                        log_fn(f"[JSON-LD] 從 @type={item.get('@type', '?')} 抽到 {len(body)} 字")
                    return text_clean.clean_text(body)
    except Exception as e:
        if log_fn:
            log_fn(f"[JSON-LD] 萃取失敗: {e}")
    return ""


def quick_content_len(source: str, log_fn=None) -> int:
    """快速估算頁面「已就緒」的主文長度（JSON-LD + 主文容器選擇器）。供深度滾動前判斷。"""
    try:
        best = len(extract_from_json_ld(source, log_fn))
        soup = BeautifulSoup(source, 'html.parser')
        for sel in ('.listicle-body-content', '.content-container',
                    '[itemprop="articleBody"]', 'article'):
            node = soup.select_one(sel)
            if node:
                best = max(best, len(node.get_text(' ', strip=True)))
        return best
    except Exception:
        return 0


def extract_from_block_payload(html: str, log_fn=None) -> str:
    """從現代框架（Next.js RSC / Condé Nast Copilot 等）序列化 block payload 抽取主文。"""
    try:
        seen = set()
        parts = []

        def _add(text):
            text = (text or "").strip()
            if len(text) >= 10 and text not in seen:
                seen.add(text)
                parts.append(text)

        pat1 = re.compile(r'\["(p|blockquote|h[1-6])","((?:[^"\\]|\\.)*)"\]', re.DOTALL)
        for m in pat1.finditer(html):
            raw = m.group(2)
            try:
                text = json.loads('"' + raw + '"')
            except Exception:
                text = raw
            _add(text)

        pat2 = re.compile(
            r'\["\$","(?:p|blockquote|h[1-6])",[^,]*,\{"[^}]*"children":"((?:[^"\\]|\\.)*)"\}',
            re.DOTALL
        )
        for m in pat2.finditer(html):
            raw = m.group(1)
            try:
                text = json.loads('"' + raw + '"')
            except Exception:
                text = raw
            _add(text)

        pat3 = re.compile(r'"([一-鿿㐀-䶿][^\\"]{14,})"')
        for m in pat3.finditer(html):
            raw = m.group(1)
            try:
                text = json.loads('"' + raw + '"')
            except Exception:
                text = raw
            _add(text)

        return text_clean.clean_text("\n".join(parts))
    except Exception as e:
        if log_fn:
            log_fn(f"[Block Payload] 抽取失敗: {e}")
        return ""


def is_listing_page(soup, log_fn=None) -> bool:
    """判定是否為列表/分類頁（多篇 <article> / article-like <li>）。"""
    if log_fn:
        log_fn("[Page Type Analysis] Starting analysis...")
    articles = soup.find_all('article', limit=10)
    if len(articles) >= 5:
        if log_fn:
            log_fn(f"[Page Type Analysis] Judgement: LISTING PAGE (found {len(articles)} <article> tags).")
        return True
    if 2 <= len(articles) < 5:
        text_lens = sorted([len(a.get_text(strip=True)) for a in articles], reverse=True)
        avg_rest = sum(text_lens[1:]) / max(len(text_lens) - 1, 1)
        if text_lens[0] > max(3 * avg_rest, 500):
            if log_fn:
                log_fn(f"[Page Type Analysis] {len(articles)} <article> tags but largest ({text_lens[0]} chars) dominates — SINGLE ARTICLE PAGE.")
        else:
            if log_fn:
                log_fn(f"[Page Type Analysis] Judgement: LISTING PAGE ({len(articles)} similarly-sized <article> tags).")
            return True
    list_items = soup.find_all('li', limit=20)
    if len(list_items) > 5:
        article_like_li = 0
        for item in list_items:
            if item.find('a') and len(item.get_text(strip=True)) > 20:
                article_like_li += 1
        if article_like_li > 5:
            if log_fn:
                log_fn(f"[Page Type Analysis] Judgement: LISTING PAGE (found {article_like_li} article-like <li> items).")
            return True
    if log_fn:
        log_fn("[Page Type Analysis] Judgement: SINGLE ARTICLE PAGE.")
    return False


def remove_cmp_containers(soup, log_fn=None) -> None:
    """抽取前移除 OneTrust / Fides / 通用 CMP 同意視窗容器（in-place decompose）。"""
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
    if removed and log_fn:
        log_fn(f"  → [CMP] Removed {removed} cookie-consent container(s) before scoring")
