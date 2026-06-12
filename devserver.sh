#!/bin/bash
# 本地開發啟動腳本
# Dev 模式下會自動以管理員身份登入（需先執行 setup_admin.sh 設定管理員）

source .venv/bin/activate
python -u -m flask --app main run --host=0.0.0.0 -p "${PORT:-8080}" --debug
