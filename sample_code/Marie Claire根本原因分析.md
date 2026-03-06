# Marie Claire 爬蟲問題根本原因分析

## 核心問題

Cloud Run 版本缺少 **Colab 版本中的關鍵噪音過濾邏輯**，導致延伸閱讀區塊被誤認為主要內容。

---

## Colab 版本 vs Cloud Run 版本的關鍵差異

### 差異 1：缺少噪音元素預過濾 ⭐ 最重要

**Colab 版本** (Spider-in-colab-20251202.py, 第 4100-4142 行):
```python
# 移除廣告、社交分享等噪音
noisy_patterns = re.compile(
    r"(ad|ads|advert|sponsor|share|social|breadcrumb|popular|"
    r"trending|recommend|related|tags|side|sidebar|comment|widget)",
    re.I
)

for el in soup.find_all(True):
    classes = el.get('class', [])
    classes_str = ' '.join(str(c) for c in classes).lower()
    id_str = str(el.get('id', '')).lower()
    
    if noisy_patterns.search(classes_str) or noisy_patterns.search(id_str):
        p_count = len(el.find_all('p'))
        text_len = len(el.get_text(strip=True))
        
        # 如果不包含重要內容（段落少且文字少），就移除
        if p_count < 3 and text_len < 400:
            el.decompose()
```

**Cloud Run 版本**: ❌ 完全沒有這個邏輯

這就是為什麼 "related"（延伸閱讀）、"recommend"（推薦文章）這類區塊沒有被過濾掉！

---

### 差異 2：候選節點篩選不同

**Colab 版本** (第 3854-3865 行):
```python
for node in soup.find_all(['article', 'section', 'div', 'main']):
    p_children = node.find_all('p', recursive=True)
    text_len = len(node.get_text(strip=True))
    if len(p_children) >= 3 or text_len > 300:  # 至少3個段落或300字
        _push([node])
```

**Cloud Run 版本** (第 563-569 行):
```python
for node in soup.find_all(['article', 'section', 'div', 'main']):
    text = node.get_text(strip=True)
    if len(text) > 300:  # 只檢查300字，沒有檢查段落數
        candidates.append(node)
```

---

### 差異 3：評分系統複雜度

**Colab 版本**: 
- 使用 `_advanced_score_node()` 進行多維度評分
- 有完整的置信度計算系統 `_calculate_confidence()`
- 考慮標題、時間標記、段落數量等結構特徵

**Cloud Run 版本**:
- 只有簡單的 `_calculate_node_score()`
- 評分維度較少，容易誤判

---

### 差異 4：列表區塊過濾時機

**Colab 版本**: 在構建候選列表時就進行多層過濾
**Cloud Run 版本**: 只在 LLM 階段過濾，heuristic 階段沒有過濾

---

## 立即修復方案

### 修改 1：在 _extract_main_text 開頭新增噪音過濾 ⭐ 必須

在 **第 538 行**之後（移除 script/style 之後）新增：

```python
def _extract_main_text(self, html: str, url: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    
    # 原有的標籤移除
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'iframe', 'header', 'aside']):
        tag.decompose()

    # ========== 新增：移除噪音元素 ==========
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
                
                # 如果段落少且文字少，就移除
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
    # ========== 新增結束 ==========
    
    # 原有的 Fides 清理
    try:
        fides_remnant = soup.find(id="fides-iframe-append")
        if fides_remnant: fides_remnant.decompose()
    except: pass
    
    # ... 後續代碼保持不變 ...
```

---

### 修改 2：改進候選節點篩選條件

將 **第 563-569 行** 改為：

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

### 修改 3：在評分前過濾 listing block

在 **第 573-578 行** 的循環中新增過濾：

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

## 為什麼 Colab 版本可以正常工作

1. **噪音過濾**: Colab 版本會提前移除包含 "related", "recommend", "popular" 等 class/id 的元素
2. **段落檢查**: Colab 版本要求節點至少有 3 個段落，而延伸閱讀區塊通常是短標題列表
3. **結構特徵**: Colab 版本在置信度計算中會檢查標題標籤、時間標記等，正文區塊通常有這些特徵

---

## 測試驗證

修改後，使用以下方式測試：

1. **URL**: https://www.marieclaire.com.tw/beauty/perfume-and-nails/80407

2. **預期結果**:
   - ✅ 抓取到 `.articleContent` 的內容
   - ✅ 開頭為：「圖片提供／宏亞香水、IG@atkinsons1799.london龐德街24號古龍水...」
   - ✅ 文字長度約 1600 字
   - ✅ 段落數約 31 個
   - ✅ 連結密度極低（只有 1 個連結）

3. **錯誤結果**（不應出現）:
   - ❌ ENTERTAINMENT 標籤
   - ❌ 《人浮於愛》、《認罪之罪》等韓劇標題
   - ❌ 「美麗佳人編輯部」重複出現
   - ❌ 大量短文章標題

---

## 優先級建議

1. **最高優先級**: 修改 1（新增噪音過濾）- 這是根本原因
2. **高優先級**: 修改 3（過濾 listing block）
3. **中優先級**: 修改 2（改進段落檢查）

建議先實施修改 1，這應該就能解決大部分問題。
