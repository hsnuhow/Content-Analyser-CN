# tests/

專案首批自動化測試（先前 repo 零測試）。聚焦**安全關鍵**與**易錯純邏輯**，
全部用 `assert` + 自帶 `__main__` runner，**本地無需安裝 pytest 也能跑**，
同時相容 `python3 -m pytest`。

## 跑法

```bash
bash tests/run.sh                 # 全部（無 pytest 也可）
python3 tests/test_net_guard.py   # 單檔
python3 -m pytest tests/          # 有 pytest 時
```

## 現有覆蓋

| 檔案 | 對象 | 重點 |
|------|------|------|
| `test_net_guard.py` | `crawler-service/net_guard.is_safe_url` | SSRF：擋 metadata/私有/loopback/link-local/IPv6 內嵌 v4 繞過，放行公網 |
| `test_pricing.py` | `app/pricing.py` | 模型名比對順序（flash-lite 不被 flash 搶先）、fallback、token/字元計費 |

## 待補（依風險優先）

- 各服務 SSRF helper（analysis `image_report._is_safe_url`、search-extent `discover._is_safe_url`）
  ——需該服務依賴（google-cloud / requests），於 CI 或裝好依賴的環境補。
- LLM JSON 清理（`_parse_llm_json` 等 4 份）——抽 `json_utils` 前先用此處做 characterization test 鎖行為，再安全去重。
- 非同步 job 狀態機、LLM 回傳 index 對位（歷史三大根因）。

> 設計原則：**先補測試鎖住現有行為，再在測試保護下重構**（巨檔拆分、去重）。
