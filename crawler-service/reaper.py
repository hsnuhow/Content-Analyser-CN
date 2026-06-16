# -*- coding: utf-8 -*-
"""
卡住任務收割（orphan/stuck job reaper）

job 卡在非終態（pending/queued/running）但 worker 已死（instance 被回收、chunk 重試耗盡等）
→ 永遠不會被標完成 → 變孤兒。本模組把「超過 STALE_MINUTES 無更新」的非終態 job 標為 failed，
之後由既有「逾 N 天刪除」的 cleanup 回收。

觸發方式（全自動、零外部排程）：
- **reap-on-submit**：每次提交新任務時先收割（掛在正常使用上）。
- **reap-on-cleanup**：cleanup 端點一併收割。
- （content-analyser 端另有 lazy 自癒：讀狀態時就地修復卡死的 dataset。）

閾值 60 分 > 最長合法無更新間隔（批次 BATCH_MAX_SECONDS=45min、單塊 CHUNK_MAX_SECONDS=25min）。
"""
import datetime

STALE_MINUTES = 60
NON_TERMINAL = ["pending", "queued", "running"]
_MAX_REAP = 200  # 單次收割上限，避免請求逾時


def reap_stale(db, collections, stale_minutes: int = STALE_MINUTES) -> int:
    """把 collections 中「非終態且超過 stale_minutes 無更新」的 job 標為 failed。回收割數。"""
    if db is None:
        return 0
    from firebase_admin import firestore
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(minutes=stale_minutes))
    reaped = 0
    for col in collections:
        try:
            q = (db.collection(col).where("status", "in", NON_TERMINAL).limit(_MAX_REAP))
            for doc in q.stream():
                d = doc.to_dict() or {}
                upd = d.get("updated_at") or d.get("created_at")
                if upd is None or upd < cutoff:
                    doc.reference.update({
                        "status": "failed",
                        "log": f"reaped：超過 {stale_minutes} 分無進度，疑 worker 中止",
                        "error": "orphaned/reaped",
                        "reaped": True,
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    })
                    reaped += 1
        except Exception as e:
            print(f"[Reaper] {col} 收割失敗（略過）: {e}", flush=True)
    if reaped:
        print(f"[Reaper] 收割 {reaped} 個卡住任務（{collections}）", flush=True)
    return reaped
