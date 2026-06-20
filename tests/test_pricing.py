# -*- coding: utf-8 -*-
"""定價估算測試（app/pricing.py）。

重點：模型名子字串比對的「由具體到一般」順序（flash-lite 不可被 flash 搶先命中）、
未知模型 fallback、token/字元計費換算。純函式、無外部依賴。

可直接執行：python3 tests/test_pricing.py　｜　相容 pytest
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pricing  # noqa: E402


def _approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_flash_basic():
    # 1000 in * 0.30/1M + 500 out * 2.50/1M = 0.0003 + 0.00125 = 0.00155
    assert _approx(pricing.est_cost_usd("gemini-2.5-flash", 1000, 500), round(0.00155, 4))

def test_flash_lite_not_shadowed_by_flash():
    # flash-lite 必須命中自己的價（0.10/0.40），不可被 'flash' 子字串搶先
    lite = pricing.est_cost_usd("gemini-2.5-flash-lite", 1_000_000, 0)
    flash = pricing.est_cost_usd("gemini-2.5-flash", 1_000_000, 0)
    assert _approx(lite, 0.10), f"flash-lite input 價應為 0.10，得 {lite}"
    assert _approx(flash, 0.30), f"flash input 價應為 0.30，得 {flash}"
    assert lite < flash

def test_pro_distinct_from_flash():
    pro = pricing.est_cost_usd("gemini-2.5-pro", 1_000_000, 0)
    assert _approx(pro, 1.25)

def test_claude_models():
    assert _approx(pricing.est_cost_usd("claude-opus-4-8", 1_000_000, 0), 5.0)
    assert _approx(pricing.est_cost_usd("claude-sonnet-4-6", 1_000_000, 0), 3.0)

def test_unknown_model_uses_fallback():
    # 未知模型用 fallback（flash 價 0.30/2.50）
    unknown = pricing.est_cost_usd("some-future-model-x", 1_000_000, 0)
    assert _approx(unknown, 0.30)

def test_embed_cost():
    # 1,000,000 字元 * 0.025/1M = 0.025
    assert _approx(pricing.est_embed_cost_usd(1_000_000), round(0.025, 4))

def test_zero_tokens():
    assert pricing.est_cost_usd("gemini-2.5-flash", 0, 0) == 0.0


def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {name}  {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {name}  (例外) {e}")
    print(f"pricing：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
