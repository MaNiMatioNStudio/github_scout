#!/bin/bash
# 自動再起動ループ — ネットワーク断などでクラッシュしても再開する
# Usage: bash run_with_retry.sh [追加の main.py 引数]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$SCRIPT_DIR/output/run_2026-03-24_2026-04-17_w30.log"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

mkdir -p "$SCRIPT_DIR/output"

echo "$(date '+%Y-%m-%d %H:%M:%S') [retry] 自動再起動ループ開始" >> "$LOG"

attempt=0
while true; do
    attempt=$((attempt + 1))
    echo "$(date '+%Y-%m-%d %H:%M:%S') [retry] attempt=$attempt 開始" >> "$LOG"

    "$PYTHON" "$SCRIPT_DIR/main.py" \
        --date-from 2026-03-24 \
        --date-to 2026-04-17 \
        --window-minutes 30 \
        "$@" >> "$LOG" 2>&1

    exit_code=$?

    # 正常終了（exit 0）なら完了
    if [ $exit_code -eq 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [retry] 完了 (exit=0)" >> "$LOG"
        echo "完了しました。"
        break
    fi

    echo "$(date '+%Y-%m-%d %H:%M:%S') [retry] クラッシュ (exit=$exit_code)、10秒後に再起動..." >> "$LOG"
    sleep 10
done
