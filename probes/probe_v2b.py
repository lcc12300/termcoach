"""P3 修正版：真实 PTY + 正确的 PS0 OSC（不用 PS1 的 \\[\\] 包裹）。
验证命令边界标记在 ignorespace/空行/多行/exit 下的可靠性。
"""
import os, pty, re, select, subprocess, tempfile, time

rc = tempfile.NamedTemporaryFile("w", suffix=".rc", delete=False)
rc.write(
    "HISTCONTROL=ignorespace\n"
    "set -o history\n"
    "PS0='$(printf \"\\033]7770;coach;S\\007\")'\n"
    "PROMPT_COMMAND='__rc=$?; printf \"\\033]7770;coach;E;%s\\007\" \"$__rc\"'\n"
)
rc.close()

pid, fd = pty.fork()
if pid == 0:
    os.execvp("bash", ["bash", "--rcfile", rc.name, "-i"])

out = bytearray()
def drain(sec):
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

drain(0.5)
for cmd in ["true\n", " false\n", "\n", "printf 'a\\nb\\n'\n", "exit 7\n"]:
    os.write(fd, cmd.encode())
    drain(0.3)
drain(0.5)
try:
    os.close(fd)
except OSError:
    pass
_, status = os.waitpid(pid, 0)
os.unlink(rc.name)

text = out.decode("utf-8", "replace")
# 按出现顺序提取标记与可见文本
events = re.findall(r"\x1b\]7770;coach;(S|E;\d+)\x07", text)
print("标记顺序:", events)
print("期望: E;0(启动) S E;0(true) S E;1(空格false) [空行:无S] E;1? S E;0(printf) [exit:无E]")
print("bash 退出码:", os.waitstatus_to_exitcode(status))
# 提取 printf 的 a b 是否在某个 S...E 块内
clean = re.sub(r"\x1b\]7770;coach;(?:S|E;\d+)\x07", "|", text)
print("可见流(节选):", repr(clean[-120:]))

print("\n=== uv / pip 可用性 ===")
for cmd in (["uv", "--version"], ["pip3", "--version"], ["python3", "-m", "pip", "--version"]):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        print(f"  {' '.join(cmd)}:", (r.stdout or r.stderr).splitlines()[0][:70])
    except FileNotFoundError:
        print(f"  {cmd[0]}: not found")