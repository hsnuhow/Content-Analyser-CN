# -*- coding: utf-8 -*-
"""LLM / embedding 估算單價（USD per 1M tokens；2026-06-19 查得最新公開定價）。

僅供成本概估——各家定價會調整，需要時更新此表即可。模型名以子字串比對（容忍版本尾綴）。
embedding 以字元計費（Vertex text-multilingual-embedding-002 約略）。
"""

# (input_per_1M, output_per_1M) USD
PRICE_PER_1M = {
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "claude-opus": (5.0, 25.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (1.0, 5.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt": (2.50, 10.0),
}
_FALLBACK = (0.30, 2.50)                 # 未知模型用 flash 價估
EMBED_PRICE_PER_1M_CHARS = 0.025         # Vertex text-multilingual-embedding-002 約 $0.000025/1k 字元

# 由具體到一般的比對順序（避免 flash 搶先命中 flash-lite）
_MATCH_ORDER = [
    ("gemini-2.5-flash-lite", "gemini-2.5-flash-lite"),
    ("gemini-2.5-pro", "gemini-2.5-pro"),
    ("gemini-2.5-flash", "gemini-2.5-flash"),
    ("flash-lite", "gemini-2.5-flash-lite"),
    ("flash", "gemini-2.5-flash"),
    ("opus", "claude-opus"),
    ("sonnet", "claude-sonnet"),
    ("haiku", "claude-haiku"),
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("gpt-4o", "gpt-4o"),
    ("gpt", "gpt"),
]


def _price_for(model: str):
    m = (model or "").lower()
    for needle, key in _MATCH_ORDER:
        if needle in m:
            return PRICE_PER_1M[key]
    return _FALLBACK


def est_cost_usd(model: str, prompt_tokens: int = 0, output_tokens: int = 0) -> float:
    """估算單次/累計 LLM 成本（USD）。"""
    try:
        pin, pout = _price_for(model)
        return round((int(prompt_tokens or 0) / 1_000_000) * pin
                     + (int(output_tokens or 0) / 1_000_000) * pout, 4)
    except Exception:
        return 0.0


def est_embed_cost_usd(chars: int = 0) -> float:
    """估算 embedding 成本（USD，以字元計）。"""
    try:
        return round((int(chars or 0) / 1_000_000) * EMBED_PRICE_PER_1M_CHARS, 4)
    except Exception:
        return 0.0
