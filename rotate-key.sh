#!/bin/bash
set -euo pipefail

# =====================================================================
# rotate-key.sh — 安全輪換服務間共用驗證金鑰（X-API-Key）
#
# 用途：CRAWLER_API_KEY / ANALYSIS_API_KEY 是「驗證方 + 呼叫方」兩個服務
#       共用的金鑰，值必須一致。隨手改一端會造成不一致而中斷。本腳本以
#       維運者本人的 gcloud 身分，原子化地完成：
#         產生新值 → 寫入 Secret Manager → 重部署兩端 → 驗證新值生效。
#
# ⛔ 安全界線（刻意設計，請勿破壞）：
#   - 金鑰只在 runtime 產生，經 stdin / 600 權限暫存檔傳遞，全程不 echo、
#     不寫入本檔、不落地、不進 git；結束前 unset。本腳本內無任何金鑰值。
#   - 僅用維運者 gcloud 身分執行；不給任何 service account 新權限 → 安全
#     界線不變（app 永遠不能自行改金鑰或重部署）。
#   - 絕不關閉/繞過 X-API-Key 驗證；驗證只看 HTTP 狀態碼，不印金鑰。
#   - 不讀取 .env 或其他 secret。
#
# 用法：
#   bash rotate-key.sh CRAWLER     # 輪換 CRAWLER_API_KEY
#   bash rotate-key.sh ANALYSIS    # 輪換 ANALYSIS_API_KEY
#
# ⚠️ 單金鑰驗證會有「兩端重部署之間」約 1–2 分鐘的短暫空窗（跨服務呼叫
#    可能 401）。請於離峰執行。零中斷需驗證方支援「過渡期接受新舊金鑰」
#    （程式改動），非本腳本範圍。
# =====================================================================

REGION="asia-east1"

TARGET="${1:-}"
case "$TARGET" in
  CRAWLER)
    SECRET="CRAWLER_API_KEY"; VALIDATOR="content-crawler"; CALLER="content-analyser"
    PROBE_PATH="/api/scrape" ;;
  ANALYSIS)
    SECRET="ANALYSIS_API_KEY"; VALIDATOR="analysis-pipeline"; CALLER="content-analyser"
    PROBE_PATH="/api/analyse" ;;
  *)
    echo "用法：bash rotate-key.sh <CRAWLER|ANALYSIS>"; exit 2 ;;
esac

PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT_ID" ]; then
  echo "Error：無法取得 GCP Project ID（先 gcloud config set project …）。"; exit 1
fi

echo "========================================================"
echo "金鑰輪換：$SECRET"
echo "Project        : $PROJECT_ID"
echo "Region         : $REGION"
echo "將重部署        : $VALIDATOR（驗證方）, $CALLER（呼叫方）"
echo "驗證端點        : $VALIDATOR$PROBE_PATH"
echo "========================================================"
echo "⚠️ 兩端重部署之間會有約 1–2 分鐘空窗，跨服務呼叫可能短暫失敗。"
read -p "確定要輪換 $SECRET？(y/N) " confirm
if [[ "$confirm" != [yY] && "$confirm" != [yY][eE][sS] ]]; then
  echo "已取消。"; exit 0
fi

# ── 1) 產生新值並寫入 Secret Manager（不 echo、不落地）──
echo ">>> [1/4] 產生新金鑰並寫入 Secret Manager…"
NEWKEY=$(openssl rand -hex 32)
ADD_OUT=$(printf '%s' "$NEWKEY" | gcloud secrets versions add "$SECRET" --data-file=- 2>&1)
echo "$ADD_OUT"
NEWVER=$(printf '%s' "$ADD_OUT" | sed -n 's/.*\[\([0-9][0-9]*\)\].*/\1/p' | head -1)
echo ">>> 新版本：${NEWVER:-未知}"

# ── 2) 重部署驗證方（重新解析 :latest 取到新值）──
echo ">>> [2/4] 重部署驗證方 $VALIDATOR…"
VIMG=$(gcloud run services describe "$VALIDATOR" --region "$REGION" \
        --format='value(spec.template.spec.containers[0].image)')
gcloud run deploy "$VALIDATOR" --image "$VIMG" --region "$REGION" --quiet >/dev/null
echo ">>> $VALIDATOR 已重部署。"

# ── 3) 重部署呼叫方 ──
echo ">>> [3/4] 重部署呼叫方 $CALLER…"
CIMG=$(gcloud run services describe "$CALLER" --region "$REGION" \
        --format='value(spec.template.spec.containers[0].image)')
gcloud run deploy "$CALLER" --image "$CIMG" --region "$REGION" --quiet >/dev/null
echo ">>> $CALLER 已重部署。"

# ── 4) 驗證：用新金鑰打驗證方受保護端點，預期非 401/403 ──
echo ">>> [4/4] 驗證新金鑰…"
VURL=$(gcloud run services describe "$VALIDATOR" --region "$REGION" \
        --format='value(status.url)')
# 金鑰寫入 600 權限暫存檔交給 curl --config，避免出現在 argv / process list。
TMPCFG=$(mktemp); chmod 600 "$TMPCFG"
printf 'header = "X-API-Key: %s"\n' "$NEWKEY" > "$TMPCFG"
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
        -X POST -H "Content-Type: application/json" -d '{}' \
        --config "$TMPCFG" "$VURL$PROBE_PATH" || echo "000")
rm -f "$TMPCFG"
unset NEWKEY

if [ "$CODE" = "401" ] || [ "$CODE" = "403" ]; then
  echo "❌ 驗證失敗（HTTP $CODE）：新金鑰未被接受。"
  echo "   回滾：停用新版本讓 :latest 退回前一版，再重部署兩端："
  echo "     gcloud secrets versions disable ${NEWVER:-<新版本>} --secret=$SECRET"
  echo "     bash rotate-key.sh $TARGET   # 或手動重部署 $VALIDATOR 與 $CALLER"
  exit 1
elif [ "$CODE" = "000" ]; then
  echo "⚠️ 無法連線驗證端點（逾時/網路）。請手動確認 $VALIDATOR 狀態。"
  exit 1
else
  echo "✅ 驗證通過（HTTP $CODE，非 401/403 → 新金鑰已生效）。"
fi

echo "========================================================"
echo "輪換完成：$SECRET 已更新並由 $VALIDATOR / $CALLER 採用。"
echo "提醒：若有外部工具（Colab / Cowork）用舊的服務金鑰，需同步更新。"
echo "========================================================"
