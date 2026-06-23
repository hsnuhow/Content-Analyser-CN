# -*- coding: utf-8 -*-
"""
Cloud Tasks 佇列輔助（讓分析在「請求生命週期內」同步跑，拿到滿 CPU）

問題：分析原本「回 202 後丟背景 daemon thread」執行。但 Cloud Run 在「沒有正在
處理的請求時會把 CPU 節流」→ 背景 thread 的純 CPU 工作（TF-IDF 斷詞、關聯規則
挖掘）被掐到約 10 倍慢，Path 1 撞 600s 看門狗 → 整個分析中止、沒產出報告。

解法（與 crawler-service/task_queue.py 同範式）：
- enqueue() 建一個 HTTP push task，由 Cloud Tasks 派送到本服務的 worker 端點。
- worker 端點在「請求生命週期內」同步跑完分析 → Cloud Run 在請求處理當下一律
  給滿 CPU → CPU 密集步驟幾秒完成，且閒置時 scale to 0、不付閒置費（優於 CPU 常駐）。

env（皆由 deploy 注入；未齊備則 tasks_enabled()=False → 呼叫端回退舊背景 thread，
不破壞 local 與「env 設了但佇列還沒建」的環境）：
  GOOGLE_CLOUD_PROJECT   GCP 專案
  TASKS_LOCATION         佇列所在 region（預設 asia-east1）
  TASKS_QUEUE            Cloud Tasks 佇列名稱（可沿用 crawler 的 queue，task 自帶目標 URL）
  WORKER_URL             本服務對外 URL（task 會 POST 到 WORKER_URL + worker_path）
  ANALYSIS_API_KEY       worker 端點驗證（task 以 X-API-Key 帶上；金鑰本身不入 body）
  ANALYSIS_USE_QUEUE     明確開關，需 =1 才啟用（佇列建好/env 齊備後才打開）
"""
import json
import os

# 單任務派送上限（Cloud Tasks HTTP 上限 30 分）。分析含 LLM 呼叫可能偏長，給足。
DISPATCH_DEADLINE_SEC = 1800


def _cfg():
    return {
        "project": os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        "location": os.environ.get("TASKS_LOCATION", "asia-east1"),
        "queue": os.environ.get("TASKS_QUEUE", ""),
        "worker_url": (os.environ.get("WORKER_URL", "") or "").rstrip("/"),
        "api_key": os.environ.get("ANALYSIS_API_KEY", ""),
    }


def tasks_enabled() -> bool:
    """佇列是否啟用。需 **明確開關** `ANALYSIS_USE_QUEUE=1` + 必要 env 齊備 +
    google-cloud-tasks 可載入。否則回退背景 thread（不破壞未設定佇列的環境）。"""
    if os.environ.get("ANALYSIS_USE_QUEUE", "0") != "1":
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
    """建一個 Cloud Tasks HTTP push task → POST {WORKER_URL}{worker_path}（帶 X-API-Key）。
    成功回 True；失敗回 False（呼叫端可回退背景 thread）。

    注意：payload 走 task body，**不得放使用者 LLM 金鑰**（金鑰存 job doc，worker 自取）。
    """
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
