#!/bin/bash
# 跑全部測試。本地無 pytest 也能用（每個 test_*.py 自帶 __main__ runner）。
# 用法：bash tests/run.sh
cd "$(dirname "$0")/.."
fail=0
for t in tests/test_*.py; do
  echo "── $t"
  python3 "$t" || fail=1
done
[ $fail -eq 0 ] && echo "✅ 全部測試通過" || echo "❌ 有測試失敗"
exit $fail
