# -*- coding: utf-8 -*-
"""
服務健康檢查客戶端（Phase 0 精簡版）

Phase 0：移除爬蟲任務呼叫邏輯。
  主程式不再協調爬蟲，此模組僅保留 health check，
  供 content-analyser 控制平面監控服務狀態使用。

Phase 3 將新增：
  - analysis_client.py：呼叫 analysis-pipeline 服務
  - 兩個服務的健康狀態彙整回傳給管理員介面
"""
import os
import requests

DEFAULT_TIMEOUT = 10


def _crawler_url() -> str:
    return os.environ.get("CRAWLER_SERVICE_URL", "").rstrip("/")


def check_crawler_health(timeout: int = DEFAULT_TIMEOUT) -> dict:
    """呼叫 content-crawler /health，回傳狀態 dict。

    回傳格式：
      正常: {"status": "ok", "service": "content-crawler", "version": "...", ...}
      異常: {"status": "unreachable" | "error" | "unknown", "error": "..."}
    """
    base = _crawler_url()
    if not base:
        return {"status": "unknown", "error": "CRAWLER_SERVICE_URL 未設定"}
    try:
        resp = requests.get(f"{base}/health", timeout=timeout)
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"status": "unreachable", "error": "無法連線至爬蟲服務"}
    except requests.exceptions.Timeout:
        return {"status": "unreachable", "error": f"連線逾時 ({timeout}s)"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
