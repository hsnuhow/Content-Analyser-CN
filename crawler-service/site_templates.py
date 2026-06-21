# -*- coding: utf-8 -*-
"""站台抽取模板（純資料常數）：各新聞/媒體站的主文容器 CSS 選擇器 + URL indicators。
從 crawler.py 抽出（純資料、無邏輯），供 crawler.py 與未來 dom_extract 共用、避免循環依賴。
模板比對「最具體優先」（網域型 indicator 勝過通用關鍵字）。
"""

SITE_TEMPLATES = {
    # ── 香港時尚站（Chrome MCP 實測 2026-06：皆 SSR、無遮罩；headless 卡死主因是廣告/追蹤 script 拖住 page-load）──
    'voguehk': {
        'indicators': ['voguehk.com'],
        'selectors': ['.article__body-main', '.editor__content', 'article'],
    },
    'popbee': {
        'indicators': ['popbee.com'],
        'selectors': ['article.post-body-article', '.post-body-content', 'article'],
    },
    # ELLE HK 與 Esquire HK 同屬 Hearst HK（eZ/Ibexa CMS + Tailwind）；段落在 .ezrichtext-field，
    #   外層 Tailwind utility class 為建構期雜湊、不穩定，只用 <article> 容器。
    'ellehk': {
        'indicators': ['elle.com.hk'],
        'selectors': ['article.ArticleItem', 'article', '.ezrichtext-field'],
    },
    'esquirehk': {
        'indicators': ['esquirehk.com'],
        'selectors': ['article.article-wrapper', 'article', '.ezrichtext-field'],
    },
    # ── A Day Magazine（WordPress + #infinite-article 無限捲動換頁）──
    # 注意：頁面使用 auto-advance JS，數秒後自動替換 DOM 並改 URL（pushState）。
    #   廣編/全頁格式用 fullPage.js，內文容器為 .fullpage-content（非標準 .entry-content）；
    #   且 JS 渲染後會清空內容，故需搭配 dom_snapshot_source（DOMContentLoaded 快照，SSR 內容）。
    'adaymag': {
        'indicators': ['adaymag.com'],
        'selectors': [
            '.fullpage-content',
            '.post-content.entry-content', '.post-content-container',
            'article.blog-post .entry-content', 'article .entry-content',
            '.entry-content', '.post-content', 'article',
        ]
    },
    'wordpress': {
        'indicators': ['wp-content', 'wp-includes', 'wordpress'],
        'selectors': ['.entry-content', '.post-content', 'article .content', '.single-content']
    },
    'pixnet': {
        'indicators': ['pixnet.net', 'pixnet'],
        'selectors': ['#article-content', '.article-content-inner', '#article-body']
    },
    'news': {
        'indicators': ['news', 'article', 'story'],
        'selectors': ['.article-body', '.story-body', '[itemprop=articleBody]', '.post-content']
    },
    'she': {
        'indicators': ['she.com'],
        'selectors': ['.content-detail.expand', '.content-detail', '.article-content']
    },
    'marieclaire': {
        'indicators': ['marieclaire.com'],
        'selectors': [
            '.articleContent',
            'div.articleContent',
            '[id^="content"]',
            '#container80407 .articleContent',
            '.articleContainer .articleContent',
            '.article-content',
            'article .content',
            '[class*="article"][class*="content"]',
            '.post-content',
            '[itemprop="articleBody"]',
            'main article',
            'article'
        ]
    },
    # ── Hearst Asia CMS（ELLE / Cosmopolitan / Harper's Bazaar 台灣版）──
    # 均使用同一套 Hearst Digital CMS，class 命名一致。
    # 注意：ELLE/Cosmo/Bazaar 台灣為 HTTP-only 站（Fastly nonssl 端點，https 連線失敗）。
    # Hearst 新版 CMS 主文容器為 .listicle-body-content / .content-container / [class*=body-content]，
    # 舊版為 .article__body-content（保留為 fallback）。
    'elle_tw': {
        # elle.com.tw = 台灣站（HTTP-only）；elle.com/tw = Hearst 國際站台灣版（HTTPS）。兩者同 CMS。
        'indicators': ['elle.com.tw', 'elle.com/tw'],
        'selectors': [
            '.standard-article-content',
            '.listicle-body-content',
            '[class*="body-content"]',
            '.content-container',
            '.article__body-content',
            '.article__body',
            '.article-content',
            '.article-body-content',
            '.article-body',
            '.article-text',
            '[class*="article__body"]',
            '[class*="article-body"]',
            '[itemprop="articleBody"]',
            'article .content',
            'article',
        ]
    },
    'cosmopolitan_tw': {
        'indicators': ['cosmopolitan.com.tw', 'cosmo.com.tw', 'cosmopolitan.com/tw'],
        'selectors': [
            '.standard-article-content',
            '.listicle-body-content',
            '[class*="body-content"]',
            '.content-container',
            '.article__body-content',
            '.article__body',
            '.article-content',
            '.article-body',
            '[class*="article__body"]',
            '[itemprop="articleBody"]',
            'article',
        ]
    },
    'harpersbazaar_tw': {
        'indicators': ['harpersbazaar.com.tw', 'harpersbazaar.com/tw'],
        'selectors': [
            '.standard-article-content',
            '.listicle-body-content',
            '[class*="body-content"]',
            '.content-container',
            '.article__body-content',
            '.article__body',
            '.article-content',
            '.article-body',
            '[class*="article__body"]',
            '[itemprop="articleBody"]',
            'article',
        ]
    },
    # ── Condé Nast 台灣（Vogue / GQ）── Next.js App Router + styled-components
    'vogue_tw': {
        'indicators': ['vogue.com.tw'],
        'selectors': [
            # 同 GQ TW（Condé Nast）：主文容器為 ArticlePageChunksContent-<hash>，用前綴屬性選擇器。
            '[class*="ArticlePageChunksContent"]', '[class*="ArticlePageChunks"]',
            '[class*="ArticleBody"]', '[class*="article-body"]',
            '[class*="RichText"]', '[class*="richtext"]',
            '[class*="ContentBody"]', '[class*="StoryBody"]',
            '.article-content', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    'gq_tw': {
        'indicators': ['gq.com.tw'],
        'selectors': [
            # GQ TW 實際主文容器為 styled-components `ArticlePageChunksContent-<hash>`；
            # hash 為建構期雜湊（每次部署變）→ 用前綴屬性選擇器抓穩定的元件名。
            '[class*="ArticlePageChunksContent"]', '[class*="ArticlePageChunks"]',
            '[class*="ArticleBody"]', '[class*="article-body"]',
            '[class*="RichText"]', '[class*="richtext"]',
            '[class*="ContentBody"]', '[class*="StoryBody"]',
            '.article-content', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 台灣汽車/設計媒體（2026-06 保時捷專案實機 DOM 確認）──
    'tvbscars': {
        # 地球黃金線（cars.tvbs.com.tw）：WordPress 風格，正文在 .entry-content。
        'indicators': ['cars.tvbs.com.tw'],
        'selectors': ['.entry-content', '.cs-entry__content-wrap',
                      '[itemprop="articleBody"]', 'article'],
    },
    'sicar': {
        # SiCAR 愛車酷：Tailwind 版型，正文容器穩定 class 為 .release_content（外層 #article_content）。
        'indicators': ['sicar.com.tw'],
        'selectors': ['.release_content', '#article_content .release_content',
                      '#article_content', 'article'],
    },
    'ppaper': {
        # ppaper.net：WordPress（Astra 主題 + Elementor）。正文在 article.elementor / .site-content。
        'indicators': ['ppaper.net'],
        'selectors': ['article.elementor', '.ast-container', '.site-content',
                      '.entry-content', 'article'],
    },
    # ── 聯合報 (udn.com) ──
    'udn': {
        'indicators': ['udn.com'],
        'selectors': [
            '.article-body__editor', '.article-content__wrapper',
            '#story_body_content', '.article-content',
            '[class*="article-body"]', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── ETtoday 新聞雲（ettoday.net → star.ettoday.net redirect）──
    'ettoday': {
        'indicators': ['ettoday.net', 'star.ettoday.net'],
        'selectors': [
            '.story', '#story', '.story-details',
            '.newsContent', '#newsContent',
            '.article-content', '.article-body',
            '[class*="story"]', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 今日新聞 (nownews.com) ──
    'nownews': {
        'indicators': ['nownews.com'],
        'selectors': [
            '#article_content', '.article_body', '.article-body',
            '.article-content', '.content-body',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 中時新聞網 (chinatimes.com) ──
    'chinatimes': {
        'indicators': ['chinatimes.com'],
        'selectors': [
            '.article-body', '.article-box',
            '.article-content', '#article-body',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── Yahoo奇摩新聞 / Yahoo Finance ──
    'yahoo_tw': {
        'indicators': ['yahoo.com/news', 'tw.yahoo.com', 'tw.finance.yahoo.com'],
        'selectors': [
            '[class*="caas-body"]', '.caas-body',
            '.article-content', '[data-component="text-block"]',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 關鍵評論網 (thenewslens.com) ──
    'thenewslens': {
        'indicators': ['thenewslens.com'],
        'selectors': [
            '.main-content', '[class*="article-body"]',
            '.article-content', '.content-body',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 遠見 (gvm.com.tw) ──
    'gvm': {
        'indicators': ['gvm.com.tw'],
        'selectors': [
            '.article_body', '.article-body', '.content-body',
            '.article-content', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 數位時代 (bnext.com.tw) ──
    'bnext': {
        'indicators': ['bnext.com.tw'],
        'selectors': [
            '.article-content__editor', '.article-content',
            '.post-content', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 風傳媒 (storm.mg) ──
    'storm_mg': {
        'indicators': ['storm.mg'],
        'selectors': [
            '.article-body', '.article-content',
            '.news-content', '.content-body',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 今周刊 (businesstoday.com.tw) ──
    'businesstoday': {
        'indicators': ['businesstoday.com.tw'],
        'selectors': [
            '.article-content', '.content-body',
            '.article-body', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 康健雜誌 (commonhealth.com.tw) ──
    'commonhealth': {
        'indicators': ['commonhealth.com.tw'],
        'selectors': [
            '.article-body', '.article-content',
            '.content-body', '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 天下雜誌 (cw.com.tw) ──
    'cw': {
        'indicators': ['cw.com.tw'],
        'selectors': [
            '.article-body', '.content', '.article-content',
            '[itemprop="articleBody"]', 'article', 'main',
        ]
    },
    # ── 商業週刊 / 親子天下 / cheers ──
    'businessweekly': {
        'indicators': ['businessweekly.com.tw'],
        'selectors': [
            '.article-body',
            '.article__content',
            '#article-body',
            '.article-content',
            '[class*="article-body"]',
            '.entry-content',
            'article',
        ]
    },
    'parenting': {
        'indicators': ['parenting.com.tw'],
        'selectors': [
            '.article-body',
            '.single-content',
            '.article-content',
            '.content-body',
            '[itemprop="articleBody"]',
            'article',
        ]
    },
    'cheers': {
        'indicators': ['cheers.com.tw'],
        'selectors': [
            '.article-body',
            '.article-content',
            '.content-detail',
            '[itemprop="articleBody"]',
            'article',
        ]
    },
    # ── 自由時報（ltn.com.tw，含 news/ec/m 等子域）──
    # 靜態 HTML 為 JS 渲染佔位，headless 執行後 .article_body 有完整全文
    # AMP 版（/amp/article/...）為靜態且有 .article_body
    'ltn': {
        'indicators': ['ltn.com.tw'],
        'selectors': [
            '.article_body', '#article_body',
            '.text', '.content940',
            '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 中央社 (cna.com.tw) ──
    # 靜態 HTML 即有完整內文，包在 article.article > .paragraph 裡
    'cna': {
        'indicators': ['cna.com.tw'],
        'selectors': [
            'article.article',
            '.centralContent',
            '[itemprop="articleBody"]',
            'article', 'main',
        ]
    },
    # ── 鏡週刊 (mirrormedia.mg) ──
    # Next.js + styled-components（class 名稱含 hash，不穩定）
    # 優先嘗試 [class*="ArticleBody"]；fallback 走 _extract_from_json_ld（JSON-LD 有完整 articleBody）
    'mirrormedia': {
        'indicators': ['mirrormedia.mg'],
        'selectors': [
            '[class*="ArticleBody"]',
            '[class*="articleBody"]',
            '[class*="article-content"]',
            '[class*="story-body"]',
            'article', 'main',
        ]
    },
    # ── TechNews 科技新報 (technews.tw) ──
    # WordPress 架構，.entry-content 是標準選擇器
    'technews': {
        'indicators': ['technews.tw'],
        'selectors': [
            '.entry-content',
            '.articleContent_text',
            '.newsLetter_articleContent',
            '.article-content',
            'article',
        ]
    },
}
