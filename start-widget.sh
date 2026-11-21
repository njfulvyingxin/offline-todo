#!/usr/bin/env bash
# 桌面卡片：其它系统没有圆角透明，会显示成方块，但功能一样。
cd "$(dirname "$0")" || exit 1
exec python3 widget.py
