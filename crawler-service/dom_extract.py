# -*- coding: utf-8 -*-
"""主文抽取協調器（Layer 2）：吃 HTML+URL，依序試 已學/快取 → 模板 → 結構化 → 啟發式評分 → LLM
五階，回 (text, resolved_by)。從 crawler.HeadlessCrawler._extract_main_text 抽出。

依賴單向（無循環）：crawler → dom_extract → {dom_score, dom_parse, text_clean, site_templates,
site_learning}（皆 leaf）。driver/LLM 相關以 callback 注入：
  ask_gemini_fn(url, soup) -> [selectors]      （crawler._ask_gemini_selector，含 LLM I/O）
  cross_domain_drift_fn(url) -> bool           （crawler._page_cross_domain_drift，讀 driver.current_url）
domain_cache（dict）就地讀寫；resolved_by 回傳（crawler 寫回 self.last_resolved_by 供 crawl_job 記帳）。
"""
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import dom_score
import dom_parse
import text_clean
from site_templates import get_site_templates

HEURISTIC_CONF_THRESHOLD = 0.55
MIN_LEARNED_CHARS = 300      # 已學/快取選擇器採用前最低字數（防誤學寬選擇器污染）

MAIN_CONTENT_SELECTORS = [
    "article", "main", "[role=main]", ".content", "#content",
    ".post-content", ".article-content", ".entry-content", ".post-body",
    "[class*=content]", "[class*=article]", "[class*=post]",
    ".article-body", "[itemprop=articleBody]", ".story__content",
    ".content-detail.expand", "#container .content-left .content-detail",
    ".article__body-content", ".article__body", ".article-body-content",
    ".article-text", "[class*='article__body']",
    ".single-content", ".content-body", ".post-article",
    "[class*='entry-body']", "[class*='article-text']",
    ".rich-text", ".richtext", "[class*='rich-text']", "[class*='richtext']",
    ".prose", "[class*='prose']",
    ".body-text", ".body-copy", "[class*='body-text']",
    "[class*='story-body']", "[class*='story-content']",
    "[data-content-type='article']", "[data-article-body]", "[data-module='article-body']",
]


def extract_main_text(html, url, *, domain_cache, genai_api_key,
                      ask_gemini_fn, cross_domain_drift_fn, log_fn=None) -> tuple:
    """回 (text, resolved_by)。resolved_by ∈ failed/learned/template/structured/llm/body_fallback/heuristic。"""
    _log = log_fn or (lambda *a, **k: None)
    # P1b 儀表化：本次抽取由哪一階解出（learned/template/structured/heuristic/llm/body_fallback/failed）。
    resolved = "failed"
    _log("=" * 80)
    _log("[DEBUG MODE] Content Extraction Process Started")
    _log("=" * 80)

    # Phase 0: 解析 HTML
    _log("\n[Phase 0] HTML Parsing")
    soup = BeautifulSoup(html, 'html.parser')
    original_html_len = len(html)
    total_elements = len(soup.find_all())
    _log(f"  → Original HTML length: {original_html_len:,} chars")
    _log(f"  → Total DOM elements: {total_elements:,}")

    # Phase 1.1: 移除基本標籤
    _log("\n[Phase 1.1] Removing basic tags (script, style, nav, footer, etc.)")
    removed_tags = []
    for tag_name in ['script', 'style', 'nav', 'footer', 'iframe', 'header', 'aside']:
        tags = soup.find_all(tag_name)
        if tags:
            removed_tags.append(f"{tag_name}({len(tags)})")
            for tag in tags:
                tag.decompose()
    _log(f"  → Removed tags: {', '.join(removed_tags) if removed_tags else 'None'}")

    # ⭐️ [v3.8] Phase 1.1b: 抽取前移除 CMP（cookie 同意）容器
    _log("\n[Phase 1.1b] Removing CMP / cookie-consent containers (OneTrust / Fides)")
    dom_parse.remove_cmp_containers(soup, _log)

    # Phase 2: 檢查緩存（含 Firestore 持久化的「已學選擇器」，跨重啟/實例記住）
    domain = urlparse(url).netloc
    if domain and domain not in domain_cache:
        try:
            from site_learning import load_learned_selectors
            learned = load_learned_selectors().get(domain)
            if learned:
                domain_cache[domain] = learned
                _log(f"[SiteLearning] 載入已學選擇器：{domain} → {learned}")
        except Exception:
            pass
    if domain in domain_cache:
        sel = domain_cache[domain]
        _log(f"\n[Phase 2] Cache Hit! Using cached selector for domain '{domain}'")
        _log(f"  → Selector: '{sel}'")
        # P4 自癒：此 cache 選擇器是否為「持久化的已學選擇器」（非模板/Phase5 暫存）→ 只對它記命中
        #   結果，連續失效 N 次自動降級重學。load_learned_selectors 有 60s 快取，查詢便宜。
        try:
            from site_learning import load_learned_selectors as _lls, note_learned_outcome as _nlo
            _is_learned = (_lls().get(domain) == sel)
        except Exception:
            _is_learned, _nlo = False, None
        node = soup.select_one(sel)
        if node:
            content = text_clean.clean_text(node.get_text("\n", strip=True))
            # 讀取端驗證：已學/快取選擇器可能是歷史誤學的寬選擇器（body/列表/cookie 區塊）。
            # 採用前檢查字數 + 非列表 + 非 cookie banner，不達標就 fallthrough 走模板/啟發式，
            # 不再「命中就無條件回傳」（修補選擇器污染整個網域的最大缺口）。
            if (len(content) >= MIN_LEARNED_CHARS
                    and not dom_score.looks_like_listing_block(node)
                    and not dom_score.looks_like_cookie_banner(content, node)):
                _log(f"  → ✅ Content extracted: {len(content)} chars")
                _log(f"  → Preview: {content[:200]}...")
                if _is_learned and _nlo:
                    _nlo(domain, True)    # P4：命中有效 → fail_count 歸零
                resolved = "learned"
                return content, resolved
            _log(f"  → ⚠️ Cached selector 命中但內容不合格"
                      f"（{len(content)} 字／列表或 cookie 區塊），改走模板/啟發式")
            if _is_learned and _nlo:
                _nlo(domain, False)       # P4：已學選擇器命中但內容不合格 → 計失效
        else:
            _log(f"  → ⚠️ Cached selector no longer matches, clearing cache")
            del domain_cache[domain]
            if _is_learned and _nlo:
                _nlo(domain, False)       # P4：已學選擇器不再匹配 → 計失效

    # Phase 2.0: 優先嘗試模板選擇器（在噪音過濾之前！）
    _log("\n[Phase 2.0] Checking for Known Site Templates (BEFORE noise filtering)")
    template_matched = None
    template_elements_to_protect = set()

    # ⭐ 比對所有命中模板，選「最具體」者：
    #    網域型 indicator（含 '.'，如 cna.com.tw）比通用關鍵字（news/article/story）更具體，
    #    避免 'news' 通用模板搶先命中 cna.com.tw/news/... 而蓋掉專屬 cna 模板。
    url_lower = url.lower()
    matched_templates = []
    _site_templates = get_site_templates()   # 後台外部化：floor + Firestore（admin 可編），60s 快取
    for tmpl_name, tmpl in _site_templates.items():
        best_ind = None
        for ind in tmpl['indicators']:
            if ind in url_lower:
                if best_ind is None or len(ind) > len(best_ind):
                    best_ind = ind
        if best_ind is not None:
            # 具體度：含 '.' 的網域型 indicator +1000 權重，再加長度
            specificity = (1000 if '.' in best_ind else 0) + len(best_ind)
            matched_templates.append((specificity, tmpl_name, tmpl))
    matched_templates.sort(key=lambda x: x[0], reverse=True)

    if matched_templates:
        for _spec, tmpl_name, tmpl in matched_templates[:1]:
            template_matched = tmpl_name
            _log(f"  → ✅ Matched template: '{tmpl_name}' (specificity={_spec})")
            if len(matched_templates) > 1:
                others = ', '.join(t[1] for t in matched_templates[1:])
                _log(f"  → （其他命中但較不具體，略過：{others}）")
            _log(f"  → Selectors to try: {tmpl['selectors']}")

            for sel in tmpl['selectors']:
                try:
                    _log(f"\n  [Trying selector: '{sel}']")
                    node = soup.select_one(sel)
                    if not node:
                        _log(f"    → ❌ Selector did not match any element")
                        continue

                    template_elements_to_protect.add(id(node))

                    text = node.get_text("\n", strip=True)
                    _log(f"    → ✅ Element found! Raw text length: {len(text)} chars, <p>: {len(node.find_all('p'))}")

                    cleaned = text_clean.clean_text(text)
                    _log(f"    → Cleaned text length: {len(cleaned)} chars")

                    if len(cleaned) >= 300:
                        _log(f"    → ✅ SUCCESS! Content sufficient (>= 300 chars), caching selector")
                        if domain:
                            domain_cache[domain] = sel
                        resolved = "template"
                        return cleaned, resolved
                    else:
                        _log(f"    → ⚠️ Content too short (< 300 chars), trying next selector")
                except Exception as e:
                    _log(f"    → ❌ Selector failed with error: {e}")
                    continue

            _log(f"\n  → ⚠️ Template selectors did not return sufficient content, falling back")
            break

    if not template_matched:
        _log(f"  → No matching template found for this URL")

    # Phase 2.05: 結構化資料優先（P2）——比通用啟發式可靠，排在其前（模板未命中/不足時）。
    #   JSON-LD articleBody（多 CMS 內嵌、雜湊站也有）/ [itemprop="articleBody"]。≥500 字才採用
    #   （高於通用 300，避免抓到摘要/teaser）。命中即標 structured 並回傳。
    _log("\n[Phase 2.05] Structured data (JSON-LD articleBody / itemprop)")
    try:
        sd_text = dom_parse.extract_from_json_ld(html, _log)
        if not (sd_text and len(sd_text) >= 500):
            sd_node = soup.select_one('[itemprop="articleBody"]')
            if sd_node:
                cand = text_clean.clean_text(sd_node.get_text("\n", strip=True))
                if (len(cand) >= 500 and not dom_score.looks_like_listing_block(sd_node)
                        and not dom_score.looks_like_cookie_banner(cand, sd_node)):
                    sd_text = cand
        if sd_text and len(sd_text) >= 500:
            _log(f"  → ✅ Structured data hit: {len(sd_text)} chars")
            resolved = "structured"
            return sd_text, resolved
        _log("  → no sufficient structured data, continue")
    except Exception as e:
        _log(f"  → structured data skipped: {e}")

    # Phase 1.2: 噪音過濾（僅在模板失敗時執行）
    _log("\n[Phase 1.2] Noise Filtering (ads, recommendations, related articles)")
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
            depth = dom_score.get_element_depth(el)
            if depth < 4:
                skipped_shallow += 1
                continue

            classes = el.get('class', [])
            classes_str = ' '.join(str(c) for c in classes).lower()
            id_str = str(el.get('id', '')).lower()

            if noisy_patterns.search(classes_str) or noisy_patterns.search(id_str):
                p_count = len(el.find_all('p'))
                text_len = len(el.get_text(strip=True))
                # 使用 AND 條件（對齊 Colab）：兩個條件都成立才移除，避免誤刪文章容器
                if p_count < 3 and text_len < 400:
                    elements_to_remove.append((el, f"noise_keyword (p={p_count}, len={text_len}, depth={depth})"))
                    continue

            text = el.get_text(" ", strip=True)
            if len(text) > 300:
                direct_links = el.find_all('a', recursive=False)
                if len(direct_links) > 5:
                    elements_to_remove.append((el, f"many_links ({len(direct_links)} direct links, depth={depth})"))
                    continue
                # 只計算明確的欄目導覽標籤（非文章內文），且需 p 數極少
                p_count_el = len(el.find_all('p'))
                if p_count_el < 2:
                    category_tags = text.upper().count('ENTERTAINMENT') + \
                                  text.upper().count('BEAUTY') + \
                                  text.upper().count('FASHION') + \
                                  text.upper().count('LIFESTYLE')
                    tag_density = category_tags / max(len(text) / 100, 1)
                    if tag_density > 1.5:
                        elements_to_remove.append((el, f"high_tag_density (density={tag_density:.2f}, tags={category_tags}, p={p_count_el}, len={len(text)}, depth={depth})"))
        except Exception:
            continue

    _log(f"  → Protected (template): {protected_count}, Skipped top-level: {skipped_top_level}, Skipped shallow: {skipped_shallow}")
    _log(f"  → Elements marked for removal: {len(elements_to_remove)}")

    for el, reason in elements_to_remove:
        try:
            if el and hasattr(el, 'decompose'):
                el.decompose()
        except Exception:
            pass

    # Phase 2.1: 構建候選列表
    _log("\n[Phase 2.1] Building Candidate List")
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
            _log(f"  → Added {count} candidates from: {source}")

    if template_matched:
        tmpl = _site_templates.get(template_matched) or {"selectors": []}
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

    _log(f"\n  → Total unique candidates: {len(candidates)}")

    if not candidates:
        _log("\n[WARNING] No candidates found! Falling back to full body text")
        body_text = text_clean.clean_text(soup.get_text("\n", strip=True))
        _log(f"  → Body text length: {len(body_text)} chars")
        resolved = "body_fallback"
        return body_text, resolved

    # Phase 3: 候選評分
    _log("\n[Phase 3] Scoring Candidates")
    scored_candidates = []
    filtered_out = []
    for node in candidates:
        if dom_score.looks_like_listing_block(node):
            filtered_out.append(node)
            continue
        score, details = dom_score.calculate_node_score(node, soup)
        if score > 0:
            scored_candidates.append((node, score, details))

    _log(f"  → Passed filtering: {len(scored_candidates)}, Filtered as listing: {len(filtered_out)}")

    if not scored_candidates:
        _log("\n[WARNING] All candidates were filtered out! Falling back to full body text")
        body_text = text_clean.clean_text(soup.get_text("\n", strip=True))
        _log(f"  → Body text length: {len(body_text)} chars")
        resolved = "body_fallback"
        return body_text, resolved

    scored_candidates.sort(key=lambda x: x[1], reverse=True)

    _log("\n  [Top 5 Candidates by Score]:")
    for i, (node, score, details) in enumerate(scored_candidates[:5], 1):
        path = dom_score.css_path(node)
        text_len = len(node.get_text(strip=True))
        _log(f"    {i}. Score: {score:.1f} | Length: {text_len} chars | Path: {path[:100]}...")

    # Phase 4: 置信度計算
    best_node, best_score, _ = scored_candidates[0]
    second_score = scored_candidates[1][1] if len(scored_candidates) > 1 else 0.0
    confidence = dom_score.calculate_confidence(best_score, second_score, best_node)

    _log(f"\n[Phase 4] Confidence: {confidence:.2%} (threshold {HEURISTIC_CONF_THRESHOLD:.2%})")

    # Phase 5: LLM 輔助（如果需要）
    if confidence < HEURISTIC_CONF_THRESHOLD and genai_api_key:
        _log(f"\n[Phase 5] Low Confidence - Requesting Gemini Assistance")
        selectors = ask_gemini_fn(url, soup)
        if selectors:
            best_llm_text, best_llm_score, best_llm_selector = None, 0.0, None
            for sel in selectors:
                try:
                    node = soup.select_one(sel)
                    if not node:
                        continue
                    if dom_score.looks_like_listing_block(node):
                        continue
                    cleaned = text_clean.clean_text(node.get_text("\n", strip=True))
                    if len(cleaned) < 200:
                        continue
                    score, _ = dom_score.calculate_node_score(node, soup)
                    _log(f"  → Selector '{sel}' scored {score:.1f}")
                    if score > best_llm_score:
                        best_llm_score, best_llm_text, best_llm_selector = score, cleaned, sel
                except Exception as e:
                    _log(f"  → Selector '{sel}' failed: {e}")
                    continue

            if best_llm_text and best_llm_score > best_score:
                _log(f"\n  → ✅ Using Gemini's choice (score {best_llm_score:.1f} > {best_score:.1f}): '{best_llm_selector}'")
                if domain and not cross_domain_drift_fn(url):
                    domain_cache[domain] = best_llm_selector
                    # ⭐ 爬蟲研究器：把 Gemini 學到的有效選擇器持久化到 Firestore，
                    #   下次（含重啟/其他實例）直接命中，不必再請 Gemini（自我修復）。
                    try:
                        from site_learning import save_learned_selector, detect_cms
                        save_learned_selector(domain, best_llm_selector, url,
                                              len(best_llm_text), detect_cms(html))
                    except Exception:
                        pass
                elif domain:
                    # 防 cloaking 污染：頁面已跨站漂移到不同網域，不把該選擇器學回原網域。
                    _log("  → ⚠️ 頁面已跨站漂移（疑 cloaking），不學此網域選擇器（防污染）")
                resolved = "llm"
                return best_llm_text, resolved
            else:
                _log(f"  → Gemini's suggestions did not improve the result")

    # 返回最佳啟發式結果
    final_content = text_clean.clean_text(best_node.get_text("\n", strip=True))
    _log(f"\n[Final Selection] Heuristic choice, score {best_score:.1f}, length {len(final_content)} chars")
    _log("=" * 80)
    _log("[EXTRACTION COMPLETE]")
    _log("=" * 80)
    resolved = "heuristic"
    return final_content, resolved

