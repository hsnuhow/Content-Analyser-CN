# -*- coding: utf-8 -*-
"""一次性 backfill：為既有 projects 補上 member_emails 陣列（N+1 修正）。

list_projects 改用 `where('member_emails','array_contains',email)` 索引查詢取代全表掃描；
既有專案沒有此欄位，需補。member_emails = list(members.keys())（只含成員，owner 另由
where('owner'==) 查，不放入此陣列）。

冪等：只更新「缺欄位或值不同」的專案，可安全重跑。乾跑預設開啟，加 --apply 才真的寫入。

執行（需 ADC / 有 Firestore 權限）：
  python3 scripts/backfill_member_emails.py            # 乾跑（只列出將變更）
  python3 scripts/backfill_member_emails.py --apply    # 實際寫入
"""
import sys

import firebase_admin
from firebase_admin import firestore


def main(apply: bool):
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    db = firestore.client()

    total = changed = 0
    for doc in db.collection('projects').stream():
        total += 1
        data = doc.to_dict() or {}
        members = data.get('members') or {}
        desired = sorted(members.keys())
        current = data.get('member_emails')
        current_sorted = sorted(current) if isinstance(current, list) else None
        if current_sorted == desired:
            continue   # 已正確，跳過
        changed += 1
        print(f"  {doc.id}: member_emails {current!r} → {desired!r}"
              f"  (owner={data.get('owner')}, {len(members)} 成員)")
        if apply:
            doc.reference.update({'member_emails': list(members.keys())})

    print(f"\n專案總數 {total}；需更新 {changed}；"
          + ("已寫入。" if apply else "乾跑（未寫入）。加 --apply 才會寫。"))


if __name__ == '__main__':
    main(apply='--apply' in sys.argv)
