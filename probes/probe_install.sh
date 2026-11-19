#!/bin/bash
# CLI 安装冒烟：uv tool install → termcoach 命令可用 → drill/rec 入口正确
set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
uv tool install --force --reinstall . 2>&1 | tail -3
echo "--- which ---"
which termcoach
echo "--- help ---"
termcoach --help
echo "--- rec 非交互守卫 ---"
echo "" | termcoach rec 2>&1 || true
echo "--- drill 非交互守卫 ---"
echo "" | termcoach drill 2>&1 || true
echo "--- 模块方式 ---"
python3 -m termcoach --help >/dev/null && echo "python3 -m termcoach ok"