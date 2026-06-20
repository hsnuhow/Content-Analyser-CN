# -*- coding: utf-8 -*-
"""pytest 共用設定：sys.path + 共用 fixtures。

讓測試能 import 各服務模組（純模組直接測；需 db 的邏輯用 FakeFirestore 注入）。
本檔僅在用 pytest 跑時載入；plain-assert runner（python3 tests/test_x.py）各檔自帶 sys.path。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ('tests', 'crawler-service', 'analysis-service', 'search-extent', 'app', '.'):
    _p = os.path.join(_ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from fakes import FakeFirestore  # noqa: E402


@pytest.fixture
def make_db():
    """回傳 FakeFirestore 工廠：db = make_db({'projects': {...}})。

    用於測 db 相依邏輯（route/service）時注入假 db，不碰真 Firestore。
    """
    return FakeFirestore
