# tests/

專案測試。聚焦**安全關鍵**與**易錯純邏輯**，並提供 **db 相依邏輯的測試替身**，
作為後續重構（巨檔拆分）的安全網。兩種跑法：

## 跑法

```bash
# 1) 無需安裝（每個 test_*.py 自帶 __main__ runner）
bash tests/run.sh
python3 tests/test_net_guard.py

# 2) 正式測試環境（pytest）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                      # 跑全部
pytest -m "not integration" # 只跑單元測試（預設無 integration 標記）
```

## 測試環境機制

- `pytest.ini`：`pythonpath` 已指向各服務目錄 → 測試可直接 `import net_guard` / `import json_utils` 等。
- `tests/conftest.py`：補 sys.path + 提供 `make_db` fixture。
- `tests/fakes.py`：**`FakeFirestore` 測試替身** —— 讓需要 `db` 的邏輯（route/service）在無真 Firestore、無網路下被測。支援 `collection / document / where(==,!=,array_contains) / order_by / limit / stream / get / set / update`。
- `@pytest.mark.integration`：標記需外部依賴（Vertex/網路/已安裝服務套件）的測試。

### 測 db 相依邏輯的範式（重構安全網用）

```python
from fakes import FakeFirestore
db = FakeFirestore({'projects': {'p1': {'owner': 'a@x.com', 'member_emails': []}}})
# 把 db 注入待測函式，斷言其查詢/寫入行為（見 test_fakes.py 的 list_projects 邏輯驗證）
```

## 現有覆蓋

| 檔案 | 對象 | 重點 |
|------|------|------|
| `test_net_guard.py` | `crawler-service/net_guard.is_safe_url` | SSRF：metadata/私有/loopback/IPv6 內嵌 v4 繞過 |
| `test_pricing.py` | `app/pricing.py` | 模型比對順序（flash-lite 不被 flash 搶先）、fallback |
| `test_json_utils.py` | `analysis-service/json_utils` | LLM-JSON 清理（fence/prose/nested/None）characterization |
| `test_fakes.py` | `tests/fakes.FakeFirestore` + N+1 | 測試替身行為 + list_projects 查詢邏輯（owner + member_emails array_contains） |

## 待補（拆檔前先補對應 characterization test）

- `nlp_path.py` 拆分前：tokenization / filters / embedding 各段先鎖行為（最獨立、建議首拆）。
- `project_routes.py` / `crawler.py` 拆分前：用 `FakeFirestore` 鎖關鍵 route 行為。

> 原則：**先補測試鎖住現有行為，再在測試保護下重構。**
