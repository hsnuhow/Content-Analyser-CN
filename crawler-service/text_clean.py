# -*- coding: utf-8 -*-
"""抽取後文字清理（純函式，無 driver / 無 I/O）：去短行正規化、裁切文末樣板。

從 crawler.HeadlessCrawler 抽出（原 _clean_text / _trim_trailing_boilerplate）：對「已抽出
的文字」做確定性字串處理（非內容區塊選擇），可單元測試，與 driver 編排分離。
crawler.py 保留同名薄方法委派至此（呼叫點不變）。
"""


def clean_text(text: str) -> str:
    """去除空白行與過短行（< 4 字），段落間以雙換行連接。
    中文字 4 字以上即可成行（一個短句如「不知道。」4 字有意義）。"""
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and len(line.strip()) >= 4]
    return "\n\n".join(lines)


# 台灣新聞站常見「尾部樣板」：贊助 CTA、APP 下載、版權宣告、社群分享。
# 文章正文之後才會出現，故只在「累積足夠正文後」遇到才裁切（保守，不誤傷短文）。
TRAILING_BOILERPLATE = (
    # 中央社
    "支持中央社", "下載中央社", "一手新聞", "本網站之文字", "非經授權",
    "小額贊助", "選擇與事實站在一起", "守護新聞自由",
    # 自由時報
    "一手掌握", "點我訂閱", "點我下載", "不用抽", "你可能有興趣",
    "今日熱門新聞", "注目新聞", "Recommended by",
    # 鏡週刊
    "支持鏡週刊", "加入訂閱會員", "贊助本文",
    # 科技新報 / TechNews
    "請我們喝杯咖啡", "訂閱免費電子報", "您也可能喜歡", "科技新報粉絲團",
    "從這裡可透過", "科技新知，時時更新",
    # 通用版權/訂閱/分享（皆為文末不可能出現在正文中段的明確樣板）
    "不得轉載", "版權所有", "著作權所有", "未經授權", "禁止轉載",
    "點我加入", "訂閱電子報", "立即下載", "下載APP", "下載 APP",
    "更多內容請見", "授權轉載",
)


def trim_trailing_boilerplate(content: str, min_keep: int = 150, log_fn=None) -> str:
    """裁掉文章正文之後的尾部樣板（贊助／APP／版權等）。

    只在累積正文已達 min_keep 字後，遇到樣板行才截斷；之前的不動，
    避免短文或正文中偶然含關鍵字時被誤砍。log_fn 可選（截斷時回報）。
    """
    if not content:
        return content
    lines = content.split("\n")
    kept = []
    acc = 0
    for line in lines:
        ls = line.strip()
        if acc >= min_keep and any(bp in ls for bp in TRAILING_BOILERPLATE):
            if log_fn:
                log_fn(f"[Trim] 尾部樣板截斷於：{ls[:30]}")
            break
        kept.append(line)
        acc += len(ls)
    return "\n".join(kept).strip()
