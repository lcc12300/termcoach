"""coach.run_edit 集成冒烟：脚本化按键驱动真实 PTY，验证编辑结果与计数。

不依赖真人敲键；用 pty 把预设按键喂给 run_edit 的 stdin。
"""
import os
import pty
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from termcoach import coach
from termcoach.counter import KeyCounter


def drive(prefill, keys, chunk_split=None):
    """在子 pty 里跑 coach.run_edit，把 keys 喂进它的 stdin。

    返回 (got_line, summary, raw_output)。
    """
    pid, fd = pty.fork()
    if pid == 0:
        # 子进程：stdout 是 pty slave；run_edit 内部再 fork 一个 bash
        os.execvp(sys.executable, [sys.executable, "-c",
            "from termcoach import coach; import json, sys; "
            f"line, s = coach.run_edit({prefill!r}); "
            "sys.stderr.write('__RESULT__' + json.dumps({'line': line, 'summary': s}) + '\\n')"])
    # 父进程：等 read 提示出现，再喂按键
    out = bytearray()
    err = bytearray()
    sent = False
    deadline = time.time() + 8
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                break
            if not d:
                break
            out.extend(d)
            if not sent and b"drill>" in out:
                time.sleep(0.2)
                if chunk_split:
                    for ch in chunk_split:
                        os.write(fd, ch)
                        time.sleep(0.05)
                else:
                    os.write(fd, keys)
                sent = True
    try:
        os.close(fd)
    except OSError:
        pass
    _, status = os.waitpid(pid, 0)
    text = out.decode("utf-8", "replace")
    result = None
    if "__RESULT__" in text:
        import json
        seg = text.split("__RESULT__", 1)[1].split("\n", 1)[0]
        result = json.loads(seg)
    return result, text


def main():
    failures = []

    # 用例1：T3 用 Ctrl-W 删末尾词，应正确且计数含 C-w
    prefill = "scp hello.ko board:/tmp/ --verbose"
    # Ctrl-E 到行尾，Ctrl-W 删 --verbose，回车
    r, _ = drive(prefill, b"\x05\x17\r")
    if r is None:
        failures.append("T3: 未拿到结果")
    else:
        line = r["line"].rstrip()
        s = r["summary"]
        if line != "scp hello.ko board:/tmp/":
            failures.append(f"T3: 行错误 {line!r}")
        if s["ctrl_shortcuts"].get("C-w") != 1:
            failures.append(f"T3: C-w 计数错误 {s['ctrl_shortcuts']}")
        if not coach.TASKS[2]["check"](r["line"]):
            failures.append("T3: check 未通过（尾随空格口径）")
        print("T3 Ctrl-W:", line, s["ctrl_shortcuts"])

    # 用例2：T2 用 Ctrl-A 跳行首加 time
    prefill2 = coach.TASKS[1]["prefill"]
    r2, _ = drive(prefill2, b"\x01time \r")
    if r2 is None:
        failures.append("T2: 未拿到结果")
    else:
        if not coach.TASKS[1]["check"](r2["line"]):
            failures.append(f"T2: check 未通过 {r2['line']!r}")
        if r2["summary"]["ctrl_shortcuts"].get("C-a") != 1:
            failures.append(f"T2: C-a 计数错误 {r2['summary']['ctrl_shortcuts']}")
        print("T2 Ctrl-A:", r2["line"][:20], r2["summary"]["ctrl_shortcuts"])

    # 用例3：跨块方向键（ESC 与 [D 分开喂）应正常计数
    prefill3 = "abcdef"
    r3, _ = drive(prefill3, None, chunk_split=[b"\x1b", b"[D", b"\x1b[D", b"X", b"\r"])
    if r3 is None:
        failures.append("跨块: 未拿到结果")
    else:
        if r3["summary"]["arrow_runs"] != [2]:
            failures.append(f"跨块: arrow_runs={r3['summary']['arrow_runs']} 期望 [2]")
        if r3["summary"]["unsupported"]:
            failures.append("跨块: 被误判 unsupported")
        print("跨块方向键:", r3["line"], r3["summary"]["arrow_runs"])

    # 用例4：粘贴块内的退格不计（逐块喂入，模拟真实按键时序）
    r4, _ = drive("abc", None, chunk_split=[
        b"\x1b[200~", b"\x7f\x7f\x7f", b"\x1b[201~", b"\x7f", b"\r"])
    if r4 is None:
        failures.append("粘贴: 未拿到结果")
    else:
        if r4["summary"]["backspace_runs"] != [1]:
            failures.append(f"粘贴: backspace_runs={r4['summary']['backspace_runs']} 期望 [1]")
        print("粘贴过滤:", r4["summary"]["backspace_runs"])

    # 用例5：Ctrl-R 沉默
    r5, _ = drive("abc", b"\x12foo\r")
    if r5 is None:
        failures.append("Ctrl-R: 未拿到结果")
    else:
        if not r5["summary"]["unsupported"]:
            failures.append("Ctrl-R: 未沉默")
        print("Ctrl-R 沉默:", r5["summary"]["unsupported_reason"])

    print("\n=== 结果 ===")
    if failures:
        for f in failures:
            print("FAIL:", f)
        sys.exit(1)
    print("全部 5 个集成用例通过")


if __name__ == "__main__":
    main()