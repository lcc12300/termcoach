#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
echo "=== 真实端点复盘（逐条仅显示，不改动 findings）==="
before=$(wc -l < ~/.cache/termcoach/findings.jsonl 2>/dev/null || echo 0)
printf 'y\n\n\n\n' | termcoach review $(ls -t ~/.cache/termcoach/transcript_*.md 2>/dev/null | head -1) 2>&1
after=$(wc -l < ~/.cache/termcoach/findings.jsonl 2>/dev/null || echo 0)
echo
echo "findings 行数 before=$before after=$after（应相同：仅显示不写入）"