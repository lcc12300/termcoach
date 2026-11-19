"""v1 方案前置探针：
P1 复现评审指出的 hook 归属问题（HISTCONTROL=ignorespace / 空行 / exit 无末条 PC）
P2 验证练习 harness 核心机制：read -e -i 预填行 + PTY 按键可观测 + bracketed paste 可检测
"""
import os, pty, select, subprocess, sys, tempfile, time

print("=== P1: hook 归属 ===")
rc = tempfile.NamedTemporaryFile("w", suffix=".rc", delete=False)
rc.write(
    "HISTCONTROL=ignorespace\n"
    "set -o history\n"
    "PS0='${ __t0=$EPOCHREALTIME; }'\n"
    "PROMPT_COMMAND='__rc=$?; printf \"PC rc=%s t0=%s hist=[%s]\\n\" \"$__rc\" \"${__t0:-UNSET}\" \"$(history 1)\" >&2'\n"
)
rc.close()
# 序列: true / 带前导空格的 false / 空行 / exit 7
inp = "true\n false\n\nexit 7\n"
p = subprocess.run(["bash", "--rcfile", rc.name, "-i"], input=inp,
                   capture_output=True, text=True, timeout=15)
pc_lines = [l for l in p.stderr.splitlines() if l.startswith("PC ")]
for l in pc_lines:
    print(" ", l)
print("  bash exit code:", p.returncode)
print("  PC 条数:", len(pc_lines), "(输入 4 行: true/空格false/空行/exit 7)")
os.unlink(rc.name)

print("\n=== P2: read -e -i 练习机制 ===")
PREFILL = "cp /old/path/file.txt /backup/"
pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = "xterm"
    os.environ["INPUTRC"] = "/dev/null"  # 隔离用户自定义键位，保证默认 emacs
    os.execvp("bash", ["bash", "--norc", "--noprofile", "-c",
        f'read -e -i "{PREFILL}" -p "edit> " line; printf "GOT:[%s]\\n" "$line"'])

out = bytearray()
def drain(sec=0.4):
    end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                b = os.read(fd, 65536)
            except OSError:
                return
            if not b:
                return
            out.extend(b)

def send(b):
    os.write(fd, b)
    drain(0.15)

drain(0.6)  # 等 read 启动
bracketed_on = b"\x1b[?2004h" in out
# 编辑动作: 3 次退格 + 2 次左移 + 插入 Z + 回车
send(b"\x7f\x7f\x7f")
send(b"\x1b[D\x1b[D")
send(b"Z")
send(b"\r")
drain(0.6)
try:
    os.close(fd)
except OSError:
    pass
_, status = os.waitpid(pid, 0)
text = out.decode("utf-8", "replace")
got = [l for l in text.splitlines() if l.startswith("GOT:")]
# 期望: 删掉末尾 3 字符 "up/"，左移 2 格后插 Z
expected = PREFILL[:-3]
expected = expected[:-2] + "Z" + expected[-2:]
print("  bracketed-paste 开启序列(ESC[?2004h)出现:", bracketed_on)
print("  GOT:", got[0] if got else "(未捕获)")
print("  期望: GOT:[%s]" % expected)
print("  行内容匹配:", bool(got) and got[0] == f"GOT:[{expected}]")
print("  子进程退出码:", os.waitstatus_to_exitcode(status))