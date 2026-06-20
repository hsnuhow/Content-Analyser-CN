# -*- coding: utf-8 -*-
"""
主文大圖擷取（階段①：只取圖、不碰文字）

設計原則（與用戶確認）：
- **完全放棄文字**：本模組只負責「找出主文容器內的大圖」，不做任何正文抽取。
- **重用主文選擇器**：大圖的選擇範圍 = 第一部分文字爬蟲的主文容器
  （learned_selectors → SITE_TEMPLATE → 啟發式），容器外的 banner / icon / 縮圖一律濾掉。
- **靜態優先、Chrome 補位**：先抓靜態 HTML 解析；靜態抓不到圖才退而用 Chrome 渲染
  （JS 站 / lazyload）。把昂貴的 Chrome 限縮在真正需要的站。
- **嚴格與文字爬蟲分離**：本模組獨立端點，不在 scrape() 文字爬取流程中執行
  （用戶要求：圖片爬取不得拖累文字爬取速度）。
- **輕量過濾優先**：只做明確垃圾濾除（廣告網域 / icon-logo 路徑 / svg / 明確小尺寸 /
  srcset 取最大 / lazy 屬性 / 絕對化）。真正的「大小判定 + 直覺視覺分析」留待
  階段②（image-analyser，下載 + Pillow 量測 + Gemini）補足。

輸出：每個 URL 回 {url, status, source, count, images:[{src,width,height,alt}]}。
"""
import random
import re
import threading
import urllib.request
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from net_guard import safe_urlopen  # SSRF 安全版 urlopen（逐跳驗 redirect 目標）

from bs4 import BeautifulSoup

from crawler import (
    DEFAULT_UA, ZH_ACCEPT_LANGUAGE,
    MAIN_CONTENT_SELECTORS, SITE_TEMPLATES, get_ad_blocklist,
)

JOBS_COLLECTION = "image_extract_jobs"

MIN_DIM = 200            # 有明確 width/height 屬性且 < 此值（px）視為小圖，濾掉
MIN_CONTAINER_TEXT = 200  # 啟發式選容器時要求的最低文字量（避免選到空殼）
SAMPLE_SIZE = 10          # 單頁回傳圖數上限：蒐集全部後「隨機抽最多 N 張」（樣本更具代表性）
HARD_COLLECT_CAP = 300    # 蒐集階段安全上限（防版型異常無限蒐集），抽樣母體上限

# icon / logo / 裝飾性小圖的常見路徑關鍵字
_JUNK_PATH_RE = re.compile(
    r"(logo|icon|sprite|avatar|placeholder|loading|blank|spacer|"
    r"1x1|pixel|favicon|emoji|badge|button|social|share|thumb)", re.I)

# lazyload 常見屬性（優先於 src，src 常是佔位圖）
_LAZY_ATTRS = ["data-src", "data-original", "data-lazy", "data-lazy-src",
               "data-actualsrc", "data-echo", "data-hi-res-src"]

_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif|bmp|tiff?)(\?|$)", re.I)


def _looks_like_image_url(abs_url: str, page_url: str) -> bool:
    """合理性網：擋掉解析錯誤產生的「非圖片」URL（如 srcset 切爛後 urljoin 出的文章相對垃圾）。
    判定為圖片：路徑含圖片副檔名，或主機與文章不同（多為圖片 CDN）。"""
    try:
        p = urlparse(abs_url)
    except Exception:
        return False
    if _IMG_EXT_RE.search(p.path):
        return True
    if p.netloc and p.netloc != urlparse(page_url).netloc:
        return True
    return False


def _to_int(v) -> Optional[int]:
    try:
        return int(str(v).strip().replace("px", ""))
    except Exception:
        return None


_DESC_RE = re.compile(r"^(\d+(?:\.\d+)?)([wx])$")


def _parse_srcset(srcset: str):
    """穩健解析 srcset → [(url, score)]。

    重點：URL 本身可能含逗號（如 Hearst 的 crop 參數 `;0,0&resize=980:*`），
    故**以空白切詞**而非用逗號切（用逗號切會把含逗號的 URL 切爛 → 產出垃圾 token）。
    候選格式為「URL [描述符]」，候選間以逗號分隔；逗號會附在描述符或無描述符 URL 的尾端。
    """
    tokens = (srcset or "").split()
    out = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.endswith(","):              # 無描述符的 URL，逗號收尾 → 一個候選
            url = tok[:-1]
            if url:
                out.append((url, 0.0))
            i += 1
            continue
        url, score = tok, 0.0
        if i + 1 < len(tokens):             # 下一個 token 可能是描述符（可能帶尾逗號）
            m = _DESC_RE.match(tokens[i + 1].rstrip(","))
            if m:
                score = float(m.group(1))
                i += 2
                out.append((url, score))
                continue
        out.append((url, score))
        i += 1
    return out


def _best_from_srcset(srcset: str) -> Optional[str]:
    """取 srcset 中尺寸描述最大者。"""
    best_url, best_score = None, -1.0
    for url, score in _parse_srcset(srcset):
        if score >= best_score:
            best_url, best_score = url, score
    return best_url


def _img_best_src(img) -> Optional[str]:
    """從 <img>（含所屬 <picture>）挑「最高解析」候選 src。"""
    candidates = []
    if img.get("srcset"):
        u = _best_from_srcset(img["srcset"])
        if u:
            candidates.append(u)
    # 所屬 <picture> 的 <source srcset>（常比 img 本身高解析）
    parent = img.parent
    if parent is not None and getattr(parent, "name", "") == "picture":
        for src_tag in parent.find_all("source"):
            if src_tag.get("srcset"):
                u = _best_from_srcset(src_tag["srcset"])
                if u:
                    candidates.append(u)
    for attr in _LAZY_ATTRS:
        if img.get(attr):
            candidates.append(img[attr])
    if img.get("src"):
        candidates.append(img["src"])
    for c in candidates:
        c = (c or "").strip()
        if c and not c.startswith("data:"):
            return c
    return None


def _resolve_container(soup: BeautifulSoup, url: str,
                       log: Callable[[str], None]):
    """解析主文容器節點，重用文字爬蟲的選擇器優先序：
    learned_selectors（per-domain）→ SITE_TEMPLATE（依 URL indicator，具體度排序）
    → MAIN_CONTENT_SELECTORS 啟發式。回 (node, selector) 或 (None, None)。
    不對 soup 做 decompose（圖片需保留在 DOM）。"""
    domain = urlparse(url).netloc

    # 1) 已學選擇器
    try:
        from site_learning import load_learned_selectors
        sel = load_learned_selectors().get(domain)
        if sel:
            node = soup.select_one(sel)
            if node:
                log(f"[ImageExtract] 主文容器：已學選擇器 {domain} → {sel}")
                return node, sel
    except Exception:
        pass

    # 2) 站台模板（與 crawler._extract_main_text 同樣的具體度排序）
    url_lower = url.lower()
    matched = []
    for name, tmpl in SITE_TEMPLATES.items():
        best = None
        for ind in tmpl["indicators"]:
            if ind in url_lower and (best is None or len(ind) > len(best)):
                best = ind
        if best is not None:
            spec = (1000 if "." in best else 0) + len(best)
            matched.append((spec, name, tmpl))
    matched.sort(key=lambda x: x[0], reverse=True)
    if matched:
        _spec, name, tmpl = matched[0]
        for sel in tmpl["selectors"]:
            try:
                node = soup.select_one(sel)
            except Exception:
                continue
            if node:
                log(f"[ImageExtract] 主文容器：模板 {name} → {sel}")
                return node, sel

    # 3) 啟發式
    for sel in MAIN_CONTENT_SELECTORS:
        try:
            node = soup.select_one(sel)
        except Exception:
            continue
        if node and len(node.get_text(strip=True)) >= MIN_CONTAINER_TEXT:
            log(f"[ImageExtract] 主文容器：啟發式 → {sel}")
            return node, sel

    return None, None


def _passes_filter(cand: Dict, ad_domains: List[str]) -> bool:
    u = cand["src"]
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return False
    host = (p.netloc or "").lower()
    for d in ad_domains:
        d = d.lower().lstrip(".")
        if host == d or host.endswith("." + d):
            return False
    path = p.path.lower()
    if path.endswith(".svg"):
        return False
    if _JUNK_PATH_RE.search(path):
        return False
    w, h = cand.get("width"), cand.get("height")
    if w is not None and w < MIN_DIM:
        return False
    if h is not None and h < MIN_DIM:
        return False
    return True


def _collect_images(html: str, base_url: str,
                    log: Callable[[str], None]) -> List[Dict]:
    """從 HTML 解析主文容器、蒐集容器內合格大圖（去重、過濾、絕對化）。"""
    soup = BeautifulSoup(html, "html.parser")
    container, _sel = _resolve_container(soup, base_url, log)
    if container is None:
        log(f"[ImageExtract] 找不到主文容器，跳過：{base_url}")
        return []
    ad_domains = get_ad_blocklist()
    seen, images = set(), []
    for img in container.find_all("img"):
        raw = _img_best_src(img)
        if not raw:
            continue
        abs_url = urljoin(base_url, raw)
        key = abs_url.split("#")[0]
        if key in seen:
            continue
        if not _looks_like_image_url(abs_url, base_url):
            continue  # 擋掉解析錯誤產生的非圖片 URL（文章相對垃圾）
        cand = {
            "src": abs_url,
            "width": _to_int(img.get("width")),
            "height": _to_int(img.get("height")),
            "alt": (img.get("alt") or "").strip()[:300],
        }
        if not _passes_filter(cand, ad_domains):
            continue
        seen.add(key)
        images.append(cand)
        if len(images) >= HARD_COLLECT_CAP:
            log(f"[ImageExtract] 達蒐集安全上限 {HARD_COLLECT_CAP}，停止蒐集")
            break
    # 全部合格大圖蒐集完 → 隨機抽最多 SAMPLE_SIZE 張（樣本更具代表性，不偏向開頭）。
    # 抽後依原始版面順序排序，輸出穩定可讀。
    if len(images) > SAMPLE_SIZE:
        idx = sorted(random.sample(range(len(images)), SAMPLE_SIZE))
        sampled = [images[i] for i in idx]
        log(f"[ImageExtract] 合格大圖 {len(images)} 張 → 隨機抽 {SAMPLE_SIZE} 張")
        return sampled
    return images


def _fetch_static_html(url: str, log: Callable[[str], None]) -> Optional[str]:
    """抓靜態 HTML（不開 Chrome）。失敗回 None。"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": DEFAULT_UA, "Accept-Language": ZH_ACCEPT_LANGUAGE})
        with safe_urlopen(req, timeout=15) as resp:
            ctype = (resp.headers.get("Content-Type", "") or "").lower()
            if "html" not in ctype:
                return None
            return resp.read(5_000_000).decode("utf-8", "ignore")
    except Exception as e:
        log(f"[ImageExtract] 靜態抓取失敗（改試 Chrome）：{e}")
        return None


def _render_with_chrome(url: str, log: Callable[[str], None],
                        crawler) -> Optional[str]:
    """用 Chrome 渲染取 page_source（JS 站補位）。共用傳入的 crawler 實例。"""
    if crawler is None:
        return None
    try:
        if getattr(crawler, "driver", None) is None:
            crawler._init_driver()
        crawler._open(url)
        return crawler.driver.page_source
    except Exception as e:
        log(f"[ImageExtract] Chrome 渲染失敗：{e}")
        return None


def extract_images_from_url(url: str, log: Callable[[str], None],
                            shared_crawler=None) -> Dict:
    """單一 URL：靜態優先取圖，靜態無圖才用 Chrome 補位。
    回 {url, status, source, count, images}。"""
    images, source = [], "static"
    html = _fetch_static_html(url, log)
    if html:
        images = _collect_images(html, url, log)
    if not images and shared_crawler is not None:
        log(f"[ImageExtract] 靜態無大圖，啟用 Chrome 渲染：{url}")
        html2 = _render_with_chrome(url, log, shared_crawler)
        if html2:
            images = _collect_images(html2, url, log)
            source = "chrome"
    status = "success" if images else "empty"
    log(f"[ImageExtract] {url} → {len(images)} 圖（{source}/{status}）")
    return {"url": url, "status": status, "source": source,
            "count": len(images), "images": images}


# ──────────────────────────────────────────────────────────────────────
# 非同步：佇列分塊（並行安全，正式）+ 背景執行緒（fallback）
# ──────────────────────────────────────────────────────────────────────
import math

IMG_CHUNK_SIZE = 8        # 佇列模式：每任務處理幾個 URL（圖擷取靜態優先，較輕，可比爬蟲大）


def _write_result(db, job_id: str, idx: int, result: dict) -> None:
    """單筆結果寫入子集合 image_extract_jobs/{job_id}/results/{idx}（idx 補零、retry 冪等覆蓋）。"""
    try:
        (db.collection(JOBS_COLLECTION).document(job_id)
         .collection("results").document(f"{idx:05d}").set(result))
    except Exception as e:
        print(f"[ImageExtract] 寫入 result {idx} 失敗: {e}", flush=True)


def _extract_sequence(urls, log, record_fn) -> dict:
    """共用擷取序列：逐一擷取 urls 的大圖（靜態優先、Chrome 補位），每篇呼叫
    record_fn(local_index, result)。回 {n_images, processed}。"""
    from crawler import HeadlessCrawler
    crawler = HeadlessCrawler(log_callback=log)  # driver lazy，靜態站不開 Chrome
    n_images = 0
    try:
        for i, url in enumerate(urls):
            try:
                r = extract_images_from_url(url, log, shared_crawler=crawler)
            except Exception as e:
                r = {"url": url, "status": "failed", "source": "", "count": 0,
                     "images": [], "error": str(e)}
                log(f"[ImageExtract] {url} 例外：{e}")
            n_images += r.get("count", 0)
            record_fn(i, r)
    finally:
        try:
            crawler.close()
        except Exception:
            pass
    return {"n_images": n_images, "processed": len(urls)}


def chunk_image_urls(urls: list):
    """切塊 [(chunk_index, offset, [urls...]), ...]，供 app.py 入列。"""
    out = []
    for ci in range(math.ceil(len(urls) / IMG_CHUNK_SIZE)):
        off = ci * IMG_CHUNK_SIZE
        out.append((ci, off, urls[off:off + IMG_CHUNK_SIZE]))
    return out


def run_image_extract_batch(job_id: str, urls: list, db) -> None:
    """Fallback（佇列未啟用）：單一背景執行緒擷取所有 urls 的大圖。"""
    from firebase_admin import firestore

    def _update(**fields):
        try:
            db.collection(JOBS_COLLECTION).document(job_id).update(
                {**fields, "updated_at": firestore.SERVER_TIMESTAMP})
        except Exception as e:
            print(f"[ImageExtract] job 更新失敗: {e}", flush=True)

    def _log(msg: str):
        print(f"[ImageExtract {job_id[:8]}] {msg}", flush=True)
        _update(log=msg)

    total = len(urls)
    try:
        _update(status="running", log=f"開始擷取 {total} 個 URL 的主文大圖...")

        def record(local_i, result):
            _write_result(db, job_id, local_i, result)
            _update(progress=int((local_i + 1) / total * 100),
                    done=local_i + 1, total=total)

        out = _extract_sequence(urls, _log, record)
        _update(status="completed", progress=100, n_images=out["n_images"],
                log=f"完成：{total} 個 URL、共 {out['n_images']} 張大圖",
                completed_at=firestore.SERVER_TIMESTAMP)
    except Exception as e:
        print(f"[ImageExtract] 批次失敗: {e}", flush=True)
        _update(status="failed", log=f"批次失敗：{e}")


def _complete_image_chunk(db, job_id: str, chunk_index: int, n_chunks: int,
                          n_images: int, n_urls: int) -> None:
    """交易式記錄某塊完成 + 聚合 n_images/done；全部塊完成才標 completed。冪等。"""
    from firebase_admin import firestore
    ref = db.collection(JOBS_COLLECTION).document(job_id)
    transaction = db.transaction()

    @firestore.transactional
    def _txn(txn):
        snap = ref.get(transaction=txn)
        if not snap.exists:
            return
        d = snap.to_dict() or {}
        cd = d.get("chunks_done") or {}
        cd[str(chunk_index)] = {"img": n_images, "n": n_urls}
        tot_img = sum(v.get("img", 0) for v in cd.values())
        tot_done = sum(v.get("n", 0) for v in cd.values())
        done_chunks = len(cd)
        upd = {"chunks_done": cd, "n_images": tot_img, "done": tot_done,
               "progress": min(99, int(done_chunks / max(1, n_chunks) * 100)),
               "updated_at": firestore.SERVER_TIMESTAMP}
        if done_chunks >= n_chunks and d.get("status") not in ("failed",):
            upd["status"] = "completed"
            upd["progress"] = 100
            upd["completed_at"] = firestore.SERVER_TIMESTAMP
            upd["log"] = f"完成：{tot_done} 個 URL、共 {tot_img} 張大圖"
        elif d.get("status") not in ("completed", "failed"):
            upd["status"] = "running"
            upd["log"] = f"已完成 {done_chunks}/{n_chunks} 塊"
        txn.update(ref, upd)

    try:
        _txn(transaction)
    except Exception as e:
        print(f"[ImageExtract] 完成計數交易失敗: {e}", flush=True)


def run_image_extract_chunk(job_id: str, urls: list, chunk_index: int,
                            n_chunks: int, offset: int, db) -> None:
    """同步處理單一塊（Cloud Tasks worker 呼叫）。"""
    def _log(msg: str):
        print(f"[ImageChunk {job_id[:8]}#{chunk_index}] {msg}", flush=True)

    def record(local_i, result):
        _write_result(db, job_id, offset + local_i, result)

    out = _extract_sequence(urls, _log, record)
    _complete_image_chunk(db, job_id, chunk_index, n_chunks,
                          out["n_images"], out["processed"])
    _log(f"塊完成：{out['n_images']} 張圖")
