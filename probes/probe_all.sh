#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
echo "=== 全量单测 ==="
python3 -m unittest discover -s tests -t . 2>&1 | grep -E "^(Ran|OK|FAILED|FAIL:|ERROR:)" | head -20
echo "=== 集成 ==="
PYTHONPATH=. python3 tests/test_rec_integration.py 2>&1 | tail -1
PYTHONPATH=. python3 tests/test_integration.py 2>&1 | tail -1
echo "=== 重装 CLI ==="
uv tool install --force --reinstall . 2>&1 | tail -1
echo "=== 真实 transcript 统计（分通道）==="
termcoach metrics $(ls -t ~/.cache/termcoach/transcript_*.md 2>/dev/null | head -1)