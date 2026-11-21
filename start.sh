#!/usr/bin/env bash
# offline-todo 启动脚本（macOS / Linux）
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
    exec python3 app.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python app.py "$@"
else
    echo "找不到 Python 3，先装一个：https://www.python.org/downloads/"
    exit 1
fi