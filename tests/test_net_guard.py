# -*- coding: utf-8 -*-
"""SSRF 守衛測試（crawler-service/net_guard.is_safe_url）。

涵蓋本服務最關鍵的安全邏輯：擋私有/保留/loopback/link-local（含 GCP metadata）
與 IPv6 內嵌 v4 繞過，放行合法公網。全部用 IP 字面值，不觸發 DNS / 網路。

可直接執行（無需 pytest）：python3 tests/test_net_guard.py
也相容 pytest：python3 -m pytest tests/test_net_guard.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crawler-service"))

import net_guard  # noqa: E402

ng = net_guard


def _ok(url):
    res, _ = ng.is_safe_url(url)
    return res


# ── 必須擋（不安全）──
def test_block_gcp_metadata_host():
    assert ng.is_safe_url("http://metadata.google.internal/")[0] is False

def test_block_metadata_ip():
    assert _ok("http://169.254.169.254/computeMetadata/v1/") is False

def test_block_link_local():
    assert _ok("http://169.254.1.1/") is False

def test_block_rfc1918_10():
    assert _ok("http://10.0.0.5/") is False

def test_block_rfc1918_192_168():
    assert _ok("http://192.168.1.1/admin") is False

def test_block_rfc1918_172_16():
    assert _ok("http://172.16.0.1/") is False

def test_block_loopback_v4():
    assert _ok("http://127.0.0.1:8080/") is False

def test_block_loopback_v6():
    assert _ok("http://[::1]/") is False

def test_block_link_local_v6():
    assert _ok("http://[fe80::1]/") is False

def test_block_ula_v6():
    assert _ok("http://[fc00::1]/") is False

def test_block_6to4_embedded_private():
    # 6to4(2002::/16) 內嵌 169.254.169.254 → 2002:a9fe:a9fe:: 應被擋
    assert _ok("http://[2002:a9fe:a9fe::]/") is False

def test_block_non_http_scheme():
    assert _ok("file:///etc/passwd") is False
    assert _ok("gopher://127.0.0.1/") is False

def test_block_missing_host():
    assert _ok("http:///nohost") is False


# ── 必須放行（安全公網）──
def test_allow_public_v4():
    assert _ok("http://8.8.8.8/") is True
    assert _ok("https://1.1.1.1/") is True

def test_allow_public_v6():
    # Google public DNS IPv6（全域單播）
    assert _ok("http://[2001:4860:4860::8888]/") is True


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
    print(f"net_guard SSRF：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
