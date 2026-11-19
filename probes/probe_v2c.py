"""P5: 命令文本归属模型验证。
正确模型：E(上一个结束)→S(本命令开始) 之间的输入 = 提示符编辑 = 命令文本；
          S→E 之间的输入 = 前台程序输入（vim 等），不是命令文本。
用 cat（交互读 stdin）模拟前台程序，验证三段分离。
"""
import os, pty, re, select, time, tempfile

rc = tempfile.NamedTemporaryFile("w", suffix=".rc", delete=False)
rc.write(
    "PS0='$(printf \"\\033]7770;coach;S\\007\")'\n"
    "PROMPT_COMMAND='__rc=$?; printf \"\\033]7770;coach;E;%s\\007\" \"$__rc\"'\n"
    "PS1='PROMPT> '\n"
)
rc.close()

pid, fd = pty.fork()
if pid == 0:
    os.execvp("bash", ["bash", "--rcfile", rc.name, "-i"])

events = []  # (t, 'in'/'out', data)
t0 = time.time()
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.03)
        if r:
            try:
                b = os.read(fd, 65536)
            except OSError:
                return
            if not b:
                return
            events.append((time.time()-t0, 'out', b))

def send(s):
    b = s.encode() if isinstance(s, str) else s
    os.write(fd, b)
    events.append((time.time()-t0, 'in', b))
    pump(0.25)

pump(0.5)
send("echo hello\r")          # 命令1：提示符编辑 echo hello
pump(0.3)
send("cat\r")                 # 命令2：启动 cat
pump(0.3)
send("line-one\r")            # cat 的程序输入（S…E 之间）
send("line-two\r")
send("\x04")                  # Ctrl-D 结束 cat
pump(0.4)
send("exit\r")
pump(0.4)
try: os.close(fd)
except OSError: pass
os.waitpid(pid, 0)
os.unlink(rc.name)

# 合并流，按 OSC 标记切段，分离 in/out
stream = b"".join(d for _, _, d in events)
# 用标记切分，记录每段里的输入字节
parts = re.split(rb"\x1b\]7770;coach;(S|E;\d+)\x07", stream)
# parts: [pre, mark1, seg1, mark2, seg2, ...]
print("=== 按 OSC 切段（每段内的输入字节）===")
# 重建：遍历 events，遇到 out 里的标记就切，收集 in
seg_idx = 0
cur_in = bytearray()
labels = ["启动前"]
seg_inputs = [bytearray()]
for _, kind, d in events:
    if kind == 'in':
        seg_inputs[-1].extend(d)
    else:
        # 找标记
        for m in re.finditer(rb"\x1b\]7770;coach;(S|E;\d+)\x07", d):
            labels.append(m.group(1).decode())
            seg_inputs.append(bytearray())
for lab, si in zip(labels, seg_inputs):
    print(f"  [{lab}] 输入: {bytes(si)!r}")

print("\n期望:")
print("  [E;0] 段输入 = b'echo hello\\r'  ← 命令1文本（提示符编辑）")
print("  [S] 段输入 = b'cat\\r'  ← 命令2文本")
print("  [E;0] 段输入 = b'line-one\\rline-two\\r\\x04'  ← cat 程序输入，非命令文本")