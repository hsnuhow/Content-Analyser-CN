# 給 GEMINI 的完整修復指令

## 問題
Cloud Run 版本缺少 Colab 版本中的**噪音過濾邏輯**，導致「延伸閱讀」、「推薦文章」等區塊被誤判為正文。

---

## 修復方案（3 個修改）

### 修改 1：新增噪音元素過濾（第 538 行之後）⭐ 最重要

找到 `_extract_main_text` 函數中的這段代碼：

```python
def _extract_main_text(self, html: str, url: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'iframe', 'header', 'aside']):
        tag.decompose()

    try:
        fides_remnant = soup.find(id="fides-iframe-append")
        if fides_remnant: fides_remnant.decompose()
    except: pass
```

**改為：**

```python
def _extract_main_text(self, html: str, url: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'iframe', 'header', 'aside']):
        tag.decompose()

    # 新增：移除噪音元素（廣告、推薦、延伸閱讀等）
    noisy_patterns = re.compile(
        r"(ad|ads|advert|sponsor|share|social|breadcrumb|popular|"
        r"trending|recommend|related|tags|side|sidebar|comment|widget)",
        re.I
    )
    
    elements_to_remove = []
    for el in soup.find_all(True):
        try:
            if el is None or not hasattr(el, 'get'):
                continue
                
            classes = el.get('class', [])
            classes_str = ' '.join(str(c) for c in classes).lower()
            id_str = str(el.get('id', '')).lower()
            
            if noisy_patterns.search(classes_str) or noisy_patterns.search(id_str):
                try:
                    p_count = len(el.find_all('p'))
                    text_len = len(el.get_text(strip=True))
                except:
                    p_count = 0
                    text_len = 0
                
                if p_count < 3 and text_len < 400:
                    elements_to_remove.append(el)
        except:
            continue
    
    for el in elements_to_remove:
        try:
            if el and hasattr(el, 'decompose'):
                el.decompose()
        except:
            pass

    try:
        fides_remnant = soup.find(id="fides-iframe-append")
        if fides_remnant: fides_remnant.decompose()
    except: pass
```

---

### 修改 2：改進候選節點篩選（第 563-569 行）

找到這段代碼：

```python
for node in soup.find_all(['article', 'section', 'div', 'main']):
    try:
        if not node or not hasattr(node, 'get_text'): continue
        text = node.get_text(strip=True)
        if len(text) > 300:
            candidates.append(node)
    except: continue
```

**改為：**

```python
for node in soup.find_all(['article', 'section', 'div', 'main']):
    try:
        if not node or not hasattr(node, 'get_text'): 
            continue
        
        # 新增：檢查段落數
        p_children = node.find_all('p', recursive=True)
        text_len = len(node.get_text(strip=True))
        
        # 至少3個段落 OR 文字長度 > 300
        if len(p_children) >= 3 or text_len > 300:
            candidates.append(node)
    except: 
        continue
```

---

### 修改 3：在評分前過濾列表區塊（第 573-578 行）

找到這段代碼：

```python
best_node, best_score = None, 0
seen = set()
for node in candidates:
    if id(node) in seen: continue
    seen.add(id(node))
    score = self._calculate_node_score(node, soup)
    if score > best_score:
        best_score, best_node = score, node
```

**改為：**

```python
best_node, best_score = None, 0
seen = set()
for node in candidates:
    if id(node) in seen: 
        continue
    seen.add(id(node))
    
    # 新增：過濾列表區塊
    if self._looks_like_listing_block(node):
        continue
    
    score = self._calculate_node_score(node, soup)
    if score > best_score:
        best_score, best_node = score, node
```

---

## 完整修改後的 _extract_main_text 函數（完整版）

如果需要完整替換，這是修改後的完整函數：

```python
def _extract_main_text(self, html: str, url: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    
    # 移除基本噪音標籤
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'iframe', 'header', 'aside']):
        tag.decompose()

    # 移除噪音元素（廣告、推薦、延伸閱讀等）
    noisy_patterns = re.compile(
        r"(ad|ads|advert|sponsor|share|social|breadcrumb|popular|"
        r"trending|recommend|related|tags|side|sidebar|comment|widget)",
        re.I
    )
    
    elements_to_remove = []
    for el in soup.find_all(True):
        try:
            if el is None or not hasattr(el, 'get'):
                continue
                
            classes = el.get('class', [])
            classes_str = ' '.join(str(c) for c in classes).lower()
            id_str = str(el.get('id', '')).lower()
            
            if noisy_patterns.search(classes_str) or noisy_patterns.search(id_str):
                try:
                    p_count = len(el.find_all('p'))
                    text_len = len(el.get_text(strip=True))
                except:
                    p_count = 0
                    text_len = 0
                
                if p_count < 3 and text_len < 400:
                    elements_to_remove.append(el)
        except:
            continue
    
    for el in elements_to_remove:
        try:
            if el and hasattr(el, 'decompose'):
                el.decompose()
        except:
            pass

    try:
        fides_remnant = soup.find(id="fides-iframe-append")
        if fides_remnant: fides_remnant.decompose()
    except: pass

    domain = urlparse(url).netloc
    if domain in self.domain_selector_cache:
        sel = self.domain_selector_cache[domain]
        node = soup.select_one(sel)
        if node:
            return self._clean_text(node.get_text("\n", strip=True))

    candidates = []
    for tmpl in SITE_TEMPLATES.values():
        if any(ind in url.lower() or ind in str(soup).lower() for ind in tmpl['indicators']):
            for sel in tmpl['selectors']:
                nodes = soup.select(sel)
                candidates.extend(nodes)
    
    for sel in MAIN_CONTENT_SELECTORS:
        candidates.extend(soup.select(sel))
        
    for node in soup.find_all(['article', 'section', 'div', 'main']):
        try:
            if not node or not hasattr(node, 'get_text'): 
                continue
            
            p_children = node.find_all('p', recursive=True)
            text_len = len(node.get_text(strip=True))
            
            if len(p_children) >= 3 or text_len > 300:
                candidates.append(node)
        except: 
            continue

    best_node, best_score = None, 0
    seen = set()
    for node in candidates:
        if id(node) in seen: 
            continue
        seen.add(id(node))
        
        # 過濾列表區塊
        if self._looks_like_listing_block(node):
            continue
        
        score = self._calculate_node_score(node, soup)
        if score > best_score:
            best_score, best_node = score, node
    
    confidence = 1.0 if best_score > 1000 else (best_score / 1000)
    
    if confidence < HEURISTIC_CONF_THRESHOLD and HAS_GENAI and self.genai_api_key:
        selectors = self._ask_gemini_selector(url, soup)
        if selectors:
            best_llm_text, best_llm_score, best_llm_selector = None, 0.0, None
            for sel in selectors:
                try:
                    node = soup.select_one(sel)
                    if not node or self._looks_like_listing_block(node):
                        self._log(f"[LLM] Selector {sel} is a listing block, skipping.")
                        continue

                    raw = node.get_text("\n", strip=True)
                    cleaned = self._clean_text(raw)
                    if len(cleaned) < 200: continue

                    score = self._calculate_node_score(node, soup)
                    self._log(f"[LLM] Candidate selector {sel} score={score:.1f}, len={len(cleaned)}")

                    if score > best_llm_score:
                        best_llm_score, best_llm_text, best_llm_selector = score, cleaned, sel
                except Exception as e:
                    self._log(f"[LLM] Selector {sel} failed: {e}")
                    continue

            if best_llm_text and best_llm_score > best_score:
                self._log(f"[LLM] Using best selector from Gemini: {best_llm_selector}")
                self.domain_selector_cache[domain] = best_llm_selector
                return best_llm_text

    if best_node:
        raw = best_node.get_text("\n", strip=True)
        if self._looks_like_cookie_banner(raw, best_node):
            self._log(f"[Content] best_node is a Cookie Banner, discarding. Score: {best_score}")
            return ""
        return self._clean_text(raw)
        
    return self._clean_text(soup.get_text("\n", strip=True))
```

---

## 測試

修改完成後，使用此 URL 測試：
https://www.marieclaire.com.tw/beauty/perfume-and-nails/80407

預期結果：
- ✅ 抓取到香水介紹正文（約 1600 字）
- ✅ 開頭：「圖片提供／宏亞香水、IG@atkinsons1799.london龐德街24號古龍水...」
- ❌ 不應出現：ENTERTAINMENT、《人浮於愛》、韓劇標題等
