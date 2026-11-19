"""P6: 复现评审指出的两处可靠性问题。
1. OSC 标记跨读取块：recorder 按单次 data finditer，拆块会丢标记
2. session.rc 落盘残留
"""
import os, sys, tempfile
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from termcoach.recorder import Recorder

ON = b"\x1b]7770;coach;ON\x07"
S = b"\x1b]7770;coach;S\x07"
E0 = b"\x1b]7770;coach;E;0\x07"

print("=== 问题1: OSC 跨块 ===")
r = Recorder()
r.feed("out", ON)
r.feed("in", b"echo hi\r")
# S 标记拆成两块
r.feed("out", S[:8])
r.feed("out", S[8:])
r.feed("out", b"hi\n")
r.feed("out", E0[:10])
r.feed("out", E0[10:])
print("  命令块数:", len(r.commands), "(期望 1)")
if r.commands:
    c = r.commands[0]
    print("  text:", repr(c.text), "rc:", c.rc, "(期望 'echo hi', 0)")
    print("  output 含标记残留:", "\x1b]7770" in c.output)
ok1 = len(r.commands) == 1 and r.commands[0].text == "echo hi" and r.commands[0].rc == 0
print("  跨块解析正确:", ok1)

print("\n=== 问题1b: ON 标记拆块（开关失效更严重）===")
r2 = Recorder()
r2.feed("out", ON[:6])
r2.feed("out", ON[6:])
r2.feed("in", b"ls\r")
r2.feed("out", S + b"x" + E0)
print("  recording:", r2.recording, "命令块:", len(r2.commands), "(期望 True, 1)")

print("\n=== 问题2: session.rc 残留 ===")
import inspect
from termcoach import rec
src = inspect.getsource(rec.run_session)
print("  run_session 写 session.rc:", "session.rc" in src)
print("  有删除逻辑:", "unlink" in src or "remove" in src or "TemporaryFile" in src)
rc_path = os.path.expanduser("~/.cache/termcoach/session.rc")
print("  实际存在:", os.path.exists(rc_path))
if os.path.exists(rc_path):
    print("  权限:", oct(os.stat(rc_path).st_mode & 0o777))