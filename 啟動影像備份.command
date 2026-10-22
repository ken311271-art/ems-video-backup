#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# 優先使用專案虛擬環境 (.venv)，若無則使用系統 python3
if [ -f "$DIR/.venv/bin/python3" ]; then
    "$DIR/.venv/bin/python3" "$DIR/video_backup.py"
elif command -v python3 &>/dev/null; then
    python3 "$DIR/video_backup.py"
else
    osascript -e 'display alert "找不到 Python 3" message "系統未偵測到 Python 3，請先安裝 Python 或使用 Mac App 獨立版。"'
fi
