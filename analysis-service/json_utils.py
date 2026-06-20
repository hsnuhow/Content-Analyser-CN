# -*- coding: utf-8 -*-
"""LLM 回傳 JSON 的穩健清理（共用工具）。

LLM 有時在 JSON 前後加說明文字或 ```json``` fence，導致 json.loads 失敗。
本模組集中先前散落在 llm_path / synthesis / denoise / image_report 的 4 份近乎相同
實作，行為以最穩健的一份為準（含 None 防護）。純函式，只用 re / json。
"""
import re
import json

_FENCE_OPEN = re.compile(r"^```(?:json)?\s*", re.MULTILINE)
_FENCE_CLOSE = re.compile(r"\s*```\s*$", re.MULTILINE)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def clean_json_str(raw) -> str:
    """去除 markdown code fence，並抽取最外層 ``{...}`` 物件字串。

    找不到物件時回傳清理後（去 fence、strip）的原字串。raw 為 None → 視為 ""。
    """
    s = _FENCE_OPEN.sub("", (raw or "").strip())
    s = _FENCE_CLOSE.sub("", s).strip()
    m = _OBJECT.search(s)
    return m.group(0) if m else s


def parse_json_obj(raw, fallback=None):
    """clean_json_str 後 json.loads；解析失敗回 fallback（預設 None）。"""
    try:
        return json.loads(clean_json_str(raw))
    except Exception:
        return fallback
