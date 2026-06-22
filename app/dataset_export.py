# -*- coding: utf-8 -*-
"""資料集匯出層（自 project_routes.py 抽出）。

單一職責：把一個 dataset dict 序列化成下載格式（Markdown / 結構化 JSON）。
純函式、無 db / Flask 依賴，可單獨 import 與測試。
"""


def _dataset_to_markdown(dataset: dict) -> str:
    """資料集 → Markdown：成功項目逐篇（標題/網址/字數/內文），末尾附未成功清單。"""
    name = dataset.get('name', 'dataset')
    items = dataset.get('items', [])
    success = [it for it in items if it.get('status') == 'success' and it.get('content')]
    others = [it for it in items if not (it.get('status') == 'success' and it.get('content'))]

    lines = [f"# {name}", "",
             f"> 共 {dataset.get('item_count', len(items))} 個網址，成功 {len(success)} 篇", ""]
    for it in success:
        # 付費牆截斷的不完整內容明確標註，避免下游/分析誤當完整文。
        inc = '　⚠️ 內容不完整（付費牆截斷，僅預覽）' if it.get('incomplete') else ''
        lines += [f"## {it.get('title') or '(無標題)'}{inc}", "",
                  f"- 網址：{it.get('url', '')}",
                  f"- 字數：{it.get('length', '-')}"
                  + ('（不完整：付費牆）' if it.get('incomplete') else ''), "",
                  it.get('content', ''), "", "---", ""]
    if others:
        lines += ["## 未成功項目", ""]
        for it in others:
            err = f" — {it.get('error')}" if it.get('error') else ""
            lines.append(f"- [{it.get('status', '?')}] {it.get('url', '')}{err}")
        lines.append("")
    return "\n".join(lines)


def _dataset_to_json(dataset: dict) -> dict:
    """資料集 → 結構化 JSON：含全部項目（成功+失敗）。"""
    items = dataset.get('items', [])
    return {
        'dataset': dataset.get('name', ''),
        'item_count': dataset.get('item_count', len(items)),
        'succeeded': sum(1 for it in items if it.get('status') == 'success'),
        'items': [
            {
                'url': it.get('url', ''),
                'title': it.get('title', ''),
                'length': it.get('length'),
                'status': it.get('status', ''),
                'content': it.get('content', ''),
                'error': it.get('error', ''),
                'incomplete': bool(it.get('incomplete')),
                'incomplete_reason': it.get('incomplete_reason', ''),
            } for it in items
        ],
    }
