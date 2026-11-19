#!/bin/bash
set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
uv tool install --force --reinstall . 2>&1 | tail -1
echo "=== pty 里运行，数一下 ESC 序列 ==="
script -qec "termcoach tips" /dev/null > /tmp/tips_pty.txt 2>&1 || true
python3 - <<'PY'
raw = open("/tmp/tips_pty.txt", "rb").read()
print("ESC 序列个数:", raw.count(b"\x1b["))
txt = raw.decode("utf-8", "replace")
print("首行原样（转义可见）:", repr(txt.splitlines()[0][:60]))
codes = sorted({p.split("m")[0] for p in txt.split("\x1b[")[1:]})
print("出现的 SGR 参数:", codes)
PY
echo "=== 非 TTY（管道）==="
termcoach tips > /tmp/tips_pipe.txt 2>&1 || true
python3 -c "print('ESC 序列个数:', open('/tmp/tips_pipe.txt','rb').read().count(b'\x1b['))"
echo "=== findings list（pty）==="
script -qec "termcoach findings list" /dev/null > /tmp/find_pty.txt 2>&1 || true
python3 -c "print('ESC 序列个数:', open('/tmp/find_pty.txt','rb').read().count(b'\x1b['))"