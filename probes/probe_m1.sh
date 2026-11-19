#!/bin/bash
# M1 端到端冒烟：metrics → import-review(逐条确认) → findings accept → tips → 趋势
set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
uv tool install --force --reinstall . 2>&1 | tail -1

T=$(ls -t ~/.cache/termcoach/transcript_*.md 2>/dev/null | head -1)
echo "=== metrics ==="
termcoach metrics "$T"

echo "=== 构造一份 AI 分析 JSON（模拟复盘产物）==="
R=$(mktemp /tmp/review_XXXX.json)
cat > "$R" <<'JSON'
{"findings": [
  {"finding_id": "shell.readline.backspace_run",
   "title": "连续退格过多",
   "scope": "bash-readline",
   "recommendation": "删除整词时尝试 Ctrl-W",
   "verification": "下一次删除词时连续退格不超过 2 次",
   "evidence": {"max_backspace_run": 34},
   "confidence": 0.9,
   "source": "ai"}
]}
JSON
echo "=== import-review（自动答 y 确认）==="
echo "y" | termcoach import-review "$R"
rm -f "$R"

echo "=== findings accept ==="
termcoach findings accept shell.readline.backspace_run
echo "=== findings list ==="
termcoach findings list
echo "=== tips ==="
termcoach tips

echo "=== 模拟第二次复盘：reviewed improving（退格降到 2）==="
python3 - <<'PY'
from termcoach import findings as F
F.append_event("reviewed", "shell.readline.backspace_run",
               trend="improving", evidence={"max_backspace_run": 2})
PY
echo "=== tips（应显示 34 → 2 趋势）==="
termcoach tips
echo "=== 清理测试 findings（不污染真实记忆）==="
rm -f ~/.cache/termcoach/findings.jsonl
echo "smoke done"