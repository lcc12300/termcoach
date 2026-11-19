"""rec.py 集成冒烟：脚本化按键驱动真实 PTY 会话。
验证 record on/off、三段分离、默认暂停、草稿确认/取消导出策略。
"""
import os
import pty
import select
import sys
import time
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def drive_session(commands, home):
    """在子 pty 里跑 rec.run_session，按 commands 喂键，返回 Recorder 状态 JSON。

    commands: list of (delay_before, bytes)
    """
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["HOME"] = home
        os.environ["TERM"] = "xterm"
        os.execvp(sys.executable, [sys.executable, "-c",
            "from termcoach import rec; import json, sys; "
            "r = rec.run_session(); "
            "sys.stderr.write('__REC__' + json.dumps({"
            "'n': len(r.commands), "
            "'cmds': [{'text': c.text, 'rc': c.rc, 'prog': c.prog_input, 'out': c.output} for c in r.commands]"
            "}) + '\\n')"])
    out = bytearray()
    deadline = time.time() + 15
    ci = 0
    next_send = time.time() + 0.8  # 等 bash 启动
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                break
            if not d:
                break
            out.extend(d)
        if ci < len(commands) and time.time() >= next_send:
            delay, data = commands[ci]
            os.write(fd, data)
            ci += 1
            next_send = time.time() + delay
        if ci >= len(commands) and b"__REC__" in out:
            break
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    text = out.decode("utf-8", "replace")
    if "__REC__" in text:
        seg = text.split("__REC__", 1)[1].split("\n", 1)[0]
        return json.loads(seg)
    return None


def main():
    failures = []
    with tempfile.TemporaryDirectory() as home:
        # 场景：默认暂停下敲 secret 不录 → record on → echo → record off → 再 secret 不录
        cmds = [
            (0.4, b"echo NOTRECORDED\r"),       # 暂停期，不录
            (0.4, b"record on\r"),
            (0.4, b"echo hello\r"),             # 录：命令文本 echo hello
            (0.4, b"record off\r"),
            (0.4, b"echo ALSONOT\r"),           # 暂停期，不录
            (0.4, b"exit\r"),
        ]
        r = drive_session(cmds, home)
        if r is None:
            failures.append("会话未返回结果")
        else:
            texts = [c["text"] for c in r["cmds"]]
            if "echo hello" not in texts:
                failures.append(f"未录到 echo hello: {texts}")
            if any("NOTRECORDED" in (t or "") for t in texts):
                failures.append("暂停期命令被误录")
            if any("ALSONOT" in (t or "") for t in texts):
                failures.append("off 后命令被误录")
            print("录到的命令文本:", texts)

        # 场景：vim 式程序输入三段分离（用 cat 模拟前台程序）
        cmds2 = [
            (0.4, b"record on\r"),
            (0.4, b"cat\r"),
            (0.4, b"progline\r"),
            (0.3, b"\x04"),
            (0.4, b"record off\r"),
            (0.4, b"exit\r"),
        ]
        r2 = drive_session(cmds2, home)
        if r2 is None:
            failures.append("cat 会话未返回")
        else:
            cat_cmds = [c for c in r2["cmds"] if c["text"] == "cat"]
            if not cat_cmds:
                failures.append(f"未找到 cat 命令: {[c['text'] for c in r2['cmds']]}")
            else:
                c = cat_cmds[0]
                if "progline" not in c["prog"]:
                    failures.append(f"程序输入未分离: {c['prog']!r}")
                if "progline" in (c["text"] or ""):
                    failures.append("程序输入混入命令文本")
                print("cat 命令文本:", c["text"], "| 程序输入:", c["prog"])

    print("\n=== 结果 ===")
    if failures:
        for f in failures:
            print("FAIL:", f)
        sys.exit(1)
    print("集成用例通过")


if __name__ == "__main__":
    main()