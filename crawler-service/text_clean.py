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


# 通用尾部樣板（版權/訂閱/下載 CTA，文末才出現、不會在正文中段）——「通用基礎」留作 floor。
# 單一媒體專屬的樣板詞（中央社/自由/鏡週刊/TechNews 的贊助·訂閱 CTA）已外部化到 Firestore
# crawler_config/junk_keywords.boilerplate（admin 可編、加站不用部署），由呼叫端以 extra_terms 注入，
# text_clean 維持純函式。
TRAILING_BOILERPLATE = (
    "不得轉載", "版權所有", "著作權所有", "未經授權", "禁止轉載",
    "點我加入", "訂閱電子報", "立即下載", "下載APP", "下載 APP",
    "更多內容請見", "授權轉載",
)


def trim_trailing_boilerplate(content: str, min_keep: int = 150, log_fn=None,
                              extra_terms=None) -> str:
    """裁掉文章正文之後的尾部樣板（贊助／APP／版權等）。

    只在累積正文已達 min_keep 字後，遇到樣板行才截斷；之前的不動，
    避免短文或正文中偶然含關鍵字時被誤砍。log_fn 可選（截斷時回報）。
    extra_terms：呼叫端注入的額外樣板詞（單一媒體專屬，來自後台 Firestore）。
    """
    if not content:
        return content
    terms = TRAILING_BOILERPLATE + tuple(extra_terms or ())
    lines = content.split("\n")
    kept = []
    acc = 0
    for line in lines:
        ls = line.strip()
        if acc >= min_keep and any(bp in ls for bp in terms):
            if log_fn:
                log_fn(f"[Trim] 尾部樣板截斷於：{ls[:30]}")
            break
        kept.append(line)
        acc += len(ls)
    return "\n".join(kept).strip()
