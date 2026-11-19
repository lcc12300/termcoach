#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
uv tool install --force --reinstall . 2>&1 | tail -1
echo "=== 真实端点：复盘 + 看是否给出可执行练习（练习一律跳过）==="
printf 'y\n\n\n\nn\n' | timeout 120 termcoach review 2>&1 | \
  grep -E "发现:|可选的随堂练习|任务:|做完的标准|预计|已跳过|校验失败|未找到|失败" | head -20