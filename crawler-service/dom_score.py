# -*- coding: utf-8 -*-
"""DOM 節點評分（純函式，無 driver / 無 I/O）：通用啟發式抽取時，對 HTML 節點打分以挑出
正文容器。從 crawler.HeadlessCrawler 抽出（原 _calculate_*/_looks_like_*/_css_path 等），
邏輯逐字保留、可單元測試，與 driver 編排分離。

依賴方向單向：crawler.py → dom_score（本模組不 import crawler）。
crawler.py 對外被呼叫的方法保留同名薄方法委派至此，呼叫點不變。
"""
from typing import Any, Dict, Tuple


def css_path(el) -> str:
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


def get_element_depth(el) -> int:
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


def calculate_visual_weight(node, soup) -> float:
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


def calculate_dom_depth(node) -> int:
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


def calculate_paragraph_quality(node) -> float:
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


def calculate_chinese_ratio(text: str) -> float:
    if not text:
        return 0.0
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    return chinese_chars / max(len(text), 1)


def looks_like_listing_block(node, log_fn=None) -> bool:
    if len(node.find_all('article', recursive=False)) > 3 or len(node.find_all('li', recursive=False)) > 5:
        if log_fn:
            log_fn("[Filter] Node disqualified by structure (contains multiple <article> or <li>).")
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
        if log_fn:
            log_fn(f"[Filter] Node disqualified by keywords: {matched_keywords}")
        return True
    return False


def looks_like_cookie_banner(text: str, node=None) -> bool:
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


def calculate_node_score(node, soup, log_fn=None) -> Tuple[float, Dict[str, float]]:
    scores: Dict[str, float] = {}
    try:
        if not node or not hasattr(node, 'get_text'):
            return 0.0, {}
        text = node.get_text("\n", strip=True)
        text_len = len(text)
        if text_len < 100:
            return 0.0, {}
        if looks_like_cookie_banner(text, node) or looks_like_listing_block(node, log_fn):
            return 0.0, {}
        scores['text_length'] = text_len * 0.2
        scores['paragraph_quality'] = calculate_paragraph_quality(node) * 1000 * 0.25
        links = node.find_all('a')
        link_text = ''.join(a.get_text(strip=True) for a in links)
        link_density = len(link_text) / max(text_len, 1)
        scores['link_density'] = (1 - link_density) * 500 * 0.25
        depth = calculate_dom_depth(node)
        optimal_depth = 8
        depth_score = 1.0 - abs(depth - optimal_depth) / max(depth, optimal_depth)
        scores['dom_depth'] = depth_score * 300 * 0.10
        scores['visual_weight'] = calculate_visual_weight(node, soup) * 400 * 0.10
        scores['chinese_ratio'] = calculate_chinese_ratio(text) * 300 * 0.10
        total_score = sum(scores.values())
        return total_score, scores
    except Exception:
        return 0.0, {}


def calculate_confidence(best_score: float, second_score: float, best_node: Any) -> float:
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
