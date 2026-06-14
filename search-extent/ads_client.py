# -*- coding: utf-8 -*-
"""
Google Ads API 用戶端（search-extent）

以 KeywordPlanIdeaService.GenerateKeywordIdeas 由種子關鍵字取得
「關聯關鍵字 + 平均搜尋量 + 競爭度」。**唯讀**——不建立/變更任何
廣告活動、帳戶或帳單。

憑證由環境變數注入（部署時來自 Secret Manager）：
  ADS_DEVELOPER_TOKEN / ADS_CLIENT_ID / ADS_CLIENT_SECRET /
  ADS_REFRESH_TOKEN / ADS_LOGIN_CUSTOMER_ID
查詢目標帳戶 customer_id 預設用 ADS_CUSTOMER_ID，否則退回 login_customer_id（MCC）。
"""
import os

# 繁體中文語言常數、台灣地區常數（Google Ads criteria ID）
DEFAULT_LANGUAGE_ID = os.environ.get("ADS_LANGUAGE_ID", "1018")   # Chinese (traditional)
DEFAULT_GEO_IDS = [g.strip() for g in os.environ.get("ADS_GEO_IDS", "2158").split(",") if g.strip()]  # Taiwan
MAX_SEEDS = 20  # Ads API 每次最多 20 個種子


class AdsConfigError(Exception):
    """Ads 憑證缺漏或 SDK 未安裝時拋出。"""
    pass


def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _required_env() -> dict:
    return {
        "developer_token": os.environ.get("ADS_DEVELOPER_TOKEN", ""),
        "client_id": os.environ.get("ADS_CLIENT_ID", ""),
        "client_secret": os.environ.get("ADS_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("ADS_REFRESH_TOKEN", ""),
        "login_customer_id": _digits(os.environ.get("ADS_LOGIN_CUSTOMER_ID", "")),
    }


def is_configured() -> bool:
    """五個必要憑證是否齊備。"""
    return all(_required_env().values())


def _build_client():
    cfg = _required_env()
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise AdsConfigError(f"缺少 Ads 憑證：{', '.join(missing)}")
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as e:
        raise AdsConfigError(f"google-ads 套件未安裝：{e}")
    return GoogleAdsClient.load_from_dict({**cfg, "use_proto_plus": True})


def generate_keyword_ideas(seeds, language_id=None, geo_ids=None, limit=200) -> list:
    """由種子關鍵字產生關聯關鍵字點子。

    回傳 list[dict]：{text, avg_monthly_searches, competition, competition_index}，
    依平均搜尋量由高至低排序。
    """
    seeds = [s.strip() for s in (seeds or []) if s and s.strip()][:MAX_SEEDS]
    if not seeds:
        raise ValueError("seeds 不可為空")
    language_id = language_id or DEFAULT_LANGUAGE_ID
    geo_ids = geo_ids or DEFAULT_GEO_IDS

    client = _build_client()
    customer_id = (_digits(os.environ.get("ADS_CUSTOMER_ID", ""))
                   or _digits(os.environ.get("ADS_LOGIN_CUSTOMER_ID", "")))

    svc = client.get_service("KeywordPlanIdeaService")
    req = client.get_type("GenerateKeywordIdeasRequest")
    req.customer_id = customer_id
    req.language = f"languageConstants/{language_id}"
    req.geo_target_constants.extend([f"geoTargetConstants/{g}" for g in geo_ids])
    req.include_adult_keywords = False
    req.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
    req.keyword_seed.keywords.extend(seeds)

    response = svc.generate_keyword_ideas(request=req)
    out = []
    for idea in response:
        m = idea.keyword_idea_metrics
        comp = getattr(m, "competition", None)
        out.append({
            "text": idea.text,
            "avg_monthly_searches": getattr(m, "avg_monthly_searches", None),
            "competition": comp.name if comp else None,
            "competition_index": getattr(m, "competition_index", None),
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda x: x.get("avg_monthly_searches") or 0, reverse=True)
    return out
