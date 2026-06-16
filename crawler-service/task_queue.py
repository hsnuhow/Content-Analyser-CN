# -*- coding: utf-8 -*-
"""
Cloud Tasks 佇列輔助（並行安全的工作派送）

把「Chrome 重活」從『回 202 後背景執行緒』改為『佇列 → 同步 worker』：
- enqueue() 建立一個 HTTP push task，由 Cloud Tasks 限速派送到本服務的 worker 端點。
- worker 端點在「請求生命週期內」同步跑完（concurrency=1 → 每台 instance 一次只跑 1 個 Chrome）。
- Cloud Tasks 的 max-concurrent-dispatches + Cloud Run maxScale 共同封頂並行 Chrome 數 → 杜絕 OOM 疊加。

env（皆由 deploy/Secret 注入；未設定則 tasks_enabled()=False → 呼叫端回退舊背景執行緒，不破壞 local）：
  GOOGLE_CLOUD_PROJECT   GCP 專案
  TASKS_LOCATION         佇列所在 region（預設 asia-east1）
  TASKS_QUEUE            Cloud Tasks 佇列名稱
  WORKER_URL             本服務對外 URL（task 會 POST 到 WORKER_URL + worker_path）
  CRAWLER_API_KEY        worker 端點驗證（沿用，task 以 X-API-Key 帶上）
"""
import json
import os

DISPATCH_DEADLINE_SEC = 1800   # 單任務派送上限（Cloud Tasks HTTP 上限 30 分）


def _cfg():
    return {
        "project": os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        "location": os.environ.get("TASKS_LOCATION", "asia-east1"),
        "queue": os.environ.get("TASKS_QUEUE", ""),
        "worker_url": (os.environ.get("WORKER_URL", "") or "").rstrip("/"),
        "api_key": os.environ.get("CRAWLER_API_KEY", ""),
    }


def tasks_enabled() -> bool:
    """佇列是否啟用。需 **明確開關** `CRAWLER_USE_QUEUE=1`（待 Cloud Tasks 佇列建好、
    IAM 授權後才由維運者打開）+ 必要 env 齊備 + google-cloud-tasks 可載入。
    否則回退背景執行緒（不破壞未設定佇列的環境，並避免「env 設了但佇列還沒建」就讓任務全失敗）。"""
    if os.environ.get("CRAWLER_USE_QUEUE", "0") != "1":
        return False
    c = _cfg()
    if not (c["project"] and c["queue"] and c["worker_url"]):
        return False
    try:
        from google.cloud import tasks_v2  # noqa: F401
    except Exception:
        return False
    return True


def enqueue(worker_path: str, payload: dict) -> bool:
    """建立一個 Cloud Tasks HTTP push task → POST {WORKER_URL}{worker_path}（帶 X-API-Key）。
    成功回 True；失敗回 False（呼叫端可回退）。"""
    c = _cfg()
    try:
        from google.cloud import tasks_v2
        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(c["project"], c["location"], c["queue"])
        headers = {"Content-Type": "application/json"}
        if c["api_key"]:
            headers["X-API-Key"] = c["api_key"]
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": c["worker_url"] + worker_path,
                "headers": headers,
                "body": json.dumps(payload).encode("utf-8"),
            },
            "dispatch_deadline": {"seconds": DISPATCH_DEADLINE_SEC},
        }
        client.create_task(parent=parent, task=task)
        return True
    except Exception as e:
        print(f"[Tasks] enqueue 失敗（{worker_path}）：{e}", flush=True)
        return False
