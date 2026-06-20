#!/bin/bash
# =====================================================================
# rollback.sh — Cloud Run 服務流量快速回滾（拆巨檔等高風險變更的保險）
#
# Cloud Run 會保留每個 revision；回滾＝把 100% 流量切回前一個（或指定）revision，
# 不需重 build、秒級生效。程式碼層面的回退另用 git（見下方 snapshot tag）。
#
# 用法：
#   bash rollback.sh <service>                    # 回滾到「前一個」revision
#   bash rollback.sh <service> <revision>         # 回滾到指定 revision
#   bash rollback.sh <service> --list             # 只列出 revisions（不切流量）
#
# 範例：
#   bash rollback.sh content-analyser
#   bash rollback.sh analysis-pipeline analysis-pipeline-00052-pd9
#   bash rollback.sh content-analyser --list
# =====================================================================
set -e
SVC="$1"
REGION="asia-east1"

if [ -z "$SVC" ]; then
  echo "用法：bash rollback.sh <service> [revision|--list]"
  echo "  service：content-analyser / content-crawler / analysis-pipeline / search-extent"
  exit 1
fi

if [ "$2" = "--list" ]; then
  echo ">>> $SVC 的 revisions（新→舊，含目前流量%）："
  gcloud run revisions list --service "$SVC" --region "$REGION" \
    --sort-by '~metadata.creationTimestamp' \
    --format 'table(metadata.name, status.conditions[0].lastTransitionTime, spec.containers[0].image)' | head -12
  echo ""
  echo ">>> 目前流量分配："
  gcloud run services describe "$SVC" --region "$REGION" \
    --format 'value(status.traffic)'
  exit 0
fi

TARGET="$2"
if [ -z "$TARGET" ]; then
  # 取建立時間第 2 新的 revision = 「前一個」
  TARGET=$(gcloud run revisions list --service "$SVC" --region "$REGION" \
    --sort-by '~metadata.creationTimestamp' --format 'value(metadata.name)' | sed -n '2p')
  if [ -z "$TARGET" ]; then
    echo "找不到前一個 revision（可能只有一個）。用 --list 查看。"
    exit 1
  fi
  echo ">>> 未指定 revision，回滾到前一個：$TARGET"
fi

echo ">>> 將 $SVC 流量 100% 切回：$TARGET"
read -p "確定回滾？(y/N) " confirm
if [[ $confirm != [yY] && $confirm != [yY][eE][sS] ]]; then
  echo "已取消。"
  exit 0
fi

gcloud run services update-traffic "$SVC" --region "$REGION" --to-revisions "$TARGET=100"
echo "✅ 已回滾。用 'bash rollback.sh $SVC --list' 確認流量。"
