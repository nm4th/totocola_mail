#!/bin/bash
# ととコーラ メルマガ自動生成 ラッパースクリプト
# launchdから呼び出される

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/newsletter.log"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 実行開始 =====" >> "$LOG_FILE"

# ANTHROPIC_API_KEYを読み込む（~/.zshrcや~/.bash_profileから）
if [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc" 2>/dev/null
elif [ -f "$HOME/.bash_profile" ]; then
    source "$HOME/.bash_profile" 2>/dev/null
fi

# .envファイルがあればそちらも読む
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY が設定されていません" >> "$LOG_FILE"
    exit 1
fi

cd "$SCRIPT_DIR"
python3 generate_newsletter.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') 正常終了 =====" >> "$LOG_FILE"
else
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') 異常終了 (code: $EXIT_CODE) =====" >> "$LOG_FILE"
fi

echo "" >> "$LOG_FILE"
exit $EXIT_CODE
