# 為什麼 Colab 版本可以正常工作，但 Cloud Run 版本不行？

## 一句話總結

**Cloud Run 版本缺少了 Colab 版本中的「噪音元素預過濾」邏輯**，導致包含 "related"、"recommend"、"popular" 等關鍵字的延伸閱讀區塊沒有被提前移除。

---

## 詳細對比

| 功能 | Colab 版本 | Cloud Run 版本 | 影響 |
|------|-----------|---------------|------|
| **噪音元素過濾** | ✅ 有 (第 4100-4142 行) | ❌ 沒有 | **致命** - 延伸閱讀區塊未被過濾 |
| **段落數檢查** | ✅ 要求至少 3 個段落 | ❌ 只檢查文字長度 | 重要 - 短標題列表被誤判 |
| **列表區塊過濾時機** | ✅ 在候選階段就過濾 | ⚠️ 只在 LLM 階段過濾 | 中等 - heuristic 無保護 |
| **評分系統** | ✅ `_advanced_score_node` | ⚠️ 簡化版 | 次要 - 影響不大 |

---

## Colab 版本的關鍵代碼（Cloud Run 缺少的）

```python
# 這段代碼在 Colab 版本的第 4100-4142 行
# Cloud Run 版本完全沒有！

noisy_patterns = re.compile(
    r"(ad|ads|advert|sponsor|share|social|breadcrumb|popular|"
    r"trending|recommend|related|tags|side|sidebar|comment|widget)",
    re.I
)

for el in soup.find_all(True):
    classes_str = ' '.join(str(c) for c in el.get('class', [])).lower()
    id_str = str(el.get('id', '')).lower()
    
    # 如果 class 或 id 包含 "related"、"recommend" 等關鍵字
    if noisy_patterns.search(classes_str) or noisy_patterns.search(id_str):
        p_count = len(el.find_all('p'))
        text_len = len(el.get_text(strip=True))
        
        # 且不包含重要內容（段落少且文字少）
        if p_count < 3 and text_len < 400:
            el.decompose()  # 移除這個元素
```

**這就是為什麼 Colab 版本能正確過濾掉延伸閱讀區塊！**

---

## 實際案例：Marie Claire 網站

### 延伸閱讀區塊的 HTML 特徵：

```html
<div class="related-articles">  <!-- class 包含 "related" -->
  <a href="...">ENTERTAINMENT 《人浮於愛》分集劇情...</a>
  <a href="...">ENTERTAINMENT 《認罪之罪》分集劇情...</a>
  <!-- 很多短標題，但沒有完整段落 -->
</div>
```

### Colab 版本的處理流程：

1. 掃描所有元素
2. 發現 class 包含 "related"
3. 檢查內容：沒有完整段落（只有短標題）
4. ✅ **移除這個區塊**
5. 後續評分時不會考慮這個區塊

### Cloud Run 版本的處理流程：

1. 沒有預過濾步驟
2. 延伸閱讀區塊進入候選列表
3. 因為包含多個文章摘要，累積字數也不少
4. ❌ **被誤判為主要內容**

---

## 修復優先級

1. **🔴 緊急（必須）**: 新增噪音元素過濾邏輯
2. **🟡 重要**: 改進段落數檢查
3. **🟢 建議**: 在評分前增加列表區塊過濾

只要完成第 1 項修改，就能解決 90% 的問題。

---

## 快速驗證

修改後，用以下命令快速驗證：

```python
# 抓取後檢查內容
if 'ENTERTAINMENT' in content:
    print("❌ 錯誤：抓取到延伸閱讀")
elif '龐德街24號古龍水' in content:
    print("✅ 正確：抓取到正文")
```
