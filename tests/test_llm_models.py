# -*- coding: utf-8 -*-
"""llm_models characterization 測試（app/llm_models.py）。

以 mock requests 驗各供應商的解析/過濾邏輯，不打真實 API。

可直接執行：python3 tests/test_llm_models.py　｜　相容 pytest
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import llm_models  # noqa: E402


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeRequests:
    """可程式化的 requests 替身：依 url 子字串回不同回應。"""
    def __init__(self, status=200, payload=None, raise_exc=False):
        self.status = status
        self.payload = payload or {}
        self.raise_exc = raise_exc
        self.last = {}

    def get(self, url, headers=None, timeout=None):
        self.last = {'url': url, 'headers': headers or {}}
        if self.raise_exc:
            raise RuntimeError('network down')
        return _FakeResp(self.status, self.payload)


def _with(fake):
    llm_models.requests = fake
    return fake


def test_empty_key_returns_empty():
    _with(_FakeRequests())
    assert llm_models._fetch_provider_models('gemini', '') == []

def test_gemini_filters_generatecontent_and_strips_prefix():
    _with(_FakeRequests(payload={'models': [
        {'name': 'models/gemini-2.5-flash', 'supportedGenerationMethods': ['generateContent']},
        {'name': 'models/embedding-001', 'supportedGenerationMethods': ['embedContent']},
    ]}))
    out = llm_models._fetch_provider_models('gemini', 'k')
    assert out == ['gemini-2.5-flash']            # 過濾掉非 generateContent + 去 models/ 前綴

def test_gemini_key_in_header_not_url():
    fake = _with(_FakeRequests(payload={'models': []}))
    llm_models._fetch_provider_models('gemini', 'SECRET')
    assert 'SECRET' not in fake.last['url']        # 金鑰不進 URL
    assert fake.last['headers'].get('x-goog-api-key') == 'SECRET'

def test_openai_filters_chat_models_sorted():
    _with(_FakeRequests(payload={'data': [
        {'id': 'gpt-4o'}, {'id': 'o3'}, {'id': 'chatgpt-4o-latest'},
        {'id': 'text-embedding-3-small'}, {'id': 'dall-e-3'},
    ]}))
    out = llm_models._fetch_provider_models('openai', 'k')
    assert out == sorted(['gpt-4o', 'o3', 'chatgpt-4o-latest'])
    assert 'text-embedding-3-small' not in out and 'dall-e-3' not in out

def test_claude_returns_ids():
    _with(_FakeRequests(payload={'data': [{'id': 'claude-sonnet-4-5'}, {'id': 'claude-opus-4-1'}]}))
    out = llm_models._fetch_provider_models('claude', 'k')
    assert out == ['claude-sonnet-4-5', 'claude-opus-4-1']

def test_non_200_returns_empty():
    _with(_FakeRequests(status=401, payload={'error': 'bad key'}))
    assert llm_models._fetch_provider_models('gemini', 'k') == []

def test_network_exception_returns_empty():
    _with(_FakeRequests(raise_exc=True))
    assert llm_models._fetch_provider_models('openai', 'k') == []

def test_unknown_provider_returns_empty():
    _with(_FakeRequests(payload={'data': []}))
    assert llm_models._fetch_provider_models('mistral', 'k') == []


def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn(); passed += 1
        except AssertionError as e:
            failed += 1; print(f"  ✗ {name}  {e}")
        except Exception as e:
            failed += 1; print(f"  ✗ {name}  (例外) {e}")
    print(f"llm_models：{passed} passed, {failed} failed（共 {len(tests)}）")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
