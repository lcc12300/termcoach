"""termcoach 编辑教练：受控练习，只编辑不执行，只计数不落盘（除非 --save）。

机制（probe P2 已验证）：PTY 内 bash --norc --noprofile + INPUTRC=/dev/null，
每个任务用 read -e -i 预填一行，用户编辑后回车，read 返回最终行。
一次 read = 一次边界明确的编辑，无命令归属问题。
"""
import argparse
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import tty
import fcntl

from .counter import KeyCounter

# ---- 任务定义（cc 2026-11-18 确认）----
# T1: 改动在路径末段；不默认推荐可能删掉整段路径的 Ctrl-W
# T2: 行首加 time（不是 sudo），练跳行首
# T3: 删末尾参数；验收忽略尾随空格
TASKS = [
    {
        "id": "T1",
        "title": "改路径末段",
        "prefill": "cp /data/reports/summary.csv /backup/summary.csv",
        "goal": "把最后一个文件名 summary.csv 改成 final.csv（两处都改）",
        "check": lambda line: line.rstrip() == "cp /data/reports/final.csv /backup/final.csv",
        # 固定对照提示（第三问基线）：与按键统计无关的通用建议
        "fixed_hint": "路径编辑可用 Tab 补全减少击键。",
        # 统计驱动建议：仅当观察到对应低效动作才给
        "coach_rules": [
            ("backspace", "末段改名时连续退格偏多：试试 Alt-B 跳到词首再改，或 Ctrl-E 到行尾后用 Alt-B 定位最后一个文件名。"),
        ],
    },
    {
        "id": "T2",
        "title": "行首加前缀",
        "prefill": "make modules KDIR=/lib/modules/$(uname -r)/build",
        "goal": "在行首加上 time 和一个空格",
        "check": lambda line: line.rstrip() == "time make modules KDIR=/lib/modules/$(uname -r)/build",
        "fixed_hint": "行首插入可用 Ctrl-A 跳过去。",
        "coach_rules": [
            ("arrow", "你用了多次左移光标回到行首：Ctrl-A 一步到位。"),
        ],
    },
    {
        "id": "T3",
        "title": "删末尾参数",
        "prefill": "scp hello.ko board:/tmp/ --verbose",
        "goal": "删掉末尾的 --verbose",
        # 忽略尾随空格：Ctrl-W 删对参数但留下空格也算对
        "check": lambda line: line.rstrip() == "scp hello.ko board:/tmp/",
        "fixed_hint": "删词可用 Ctrl-W。",
        "coach_rules": [
            ("backspace", "逐字符退格删参数：Ctrl-W 一次删一个词（注意它按空白切词，路径中段慎用）。"),
        ],
    },
]

ENTER = (0x0D, 0x0A)


def _set_winsize(fd):
    try:
        s = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, s)
    except OSError:
        pass


def run_edit(prefill: str):
    """在 PTY 里跑一次 read -e -i，返回 (final_line, summary)。

    只转发用户输入到子进程、只把子进程输出写到 stdout；
    输入字节同时喂给 KeyCounter 计数，不保存。
    """
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm"
        os.environ["INPUTRC"] = "/dev/null"
        # 用 argv 传预填行，避免 shell 引号注入；read 不执行任何命令
        os.execvp("bash", ["bash", "--norc", "--noprofile", "-c",
                           'read -e -i "$1" -p "drill> " line; printf "\\n__GOT__%s\\n" "$line"',
                           "bash", prefill])
    _set_winsize(fd)

    counter = KeyCounter()
    got_line = None
    out_buf = bytearray()
    old_attr = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            r, _, _ = select.select([fd, sys.stdin.fileno()], [], [], 0.1)
            if fd in r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                out_buf.extend(data)
                # 从输出里提取最终行（__GOT__ 标记），不依赖输入重建
                text = out_buf.decode("utf-8", "replace")
                if "__GOT__" in text:
                    seg = text.split("__GOT__", 1)[1]
                    got_line = seg.split("\n", 1)[0].rstrip("\r")
            if sys.stdin.fileno() in r:
                try:
                    data = os.read(sys.stdin.fileno(), 65536)
                except OSError:
                    break
                if not data:  # stdin EOF（脚本化测试/管道）：转发 Ctrl-D 让 read 收尾
                    os.write(fd, b"\x04")
                    deadline = time.time() + 1.0
                    while time.time() < deadline and got_line is None:
                        rr, _, _ = select.select([fd], [], [], 0.05)
                        if rr:
                            try:
                                d = os.read(fd, 65536)
                            except OSError:
                                break
                            if not d:
                                break
                            os.write(sys.stdout.fileno(), d)
                            out_buf.extend(d)
                            t = out_buf.decode("utf-8", "replace")
                            if "__GOT__" in t:
                                got_line = t.split("__GOT__", 1)[1].split("\n", 1)[0].rstrip("\r")
                    break
                os.write(fd, data)
                counter.feed(data)
                if counter._done:
                    # 回车已转发，等子进程把 __GOT__ 吐完
                    deadline = time.time() + 1.0
                    while time.time() < deadline and got_line is None:
                        rr, _, _ = select.select([fd], [], [], 0.05)
                        if rr:
                            try:
                                d = os.read(fd, 65536)
                            except OSError:
                                break
                            if not d:
                                break
                            os.write(sys.stdout.fileno(), d)
                            out_buf.extend(d)
                            t = out_buf.decode("utf-8", "replace")
                            if "__GOT__" in t:
                                got_line = t.split("__GOT__", 1)[1].split("\n", 1)[0].rstrip("\r")
                    break
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    return got_line, counter.summary()


def _max_run(runs):
    return max(runs) if runs else 0


def coach_suggestion(task, summary):
    """统计驱动建议：只在观察到对应低效动作时给，否则沉默。"""
    if summary["unsupported"]:
        return None
    hints = []
    bs_max = _max_run(summary["backspace_runs"])
    ar_max = _max_run(summary["arrow_runs"])
    for trigger, text in task["coach_rules"]:
        if trigger == "backspace" and bs_max >= 5:
            hints.append(text)
        elif trigger == "arrow" and ar_max >= 5:
            hints.append(text)
    return " ".join(hints) if hints else None


def fmt_counts(summary):
    if summary["unsupported"]:
        return f"（本次编辑含不支持的输入：{summary['unsupported_reason']}，不评判）"
    parts = [f"总键数 {summary['total_keys']}"]
    if summary["backspace_runs"]:
        parts.append(f"退格连击 {summary['backspace_runs']}")
    if summary["arrow_runs"]:
        parts.append(f"左右移连击 {summary['arrow_runs']}")
    if summary["ctrl_shortcuts"]:
        sc = ", ".join(f"{k}×{v}" for k, v in sorted(summary["ctrl_shortcuts"].items()))
        parts.append(f"快捷键 {sc}")
    return "；".join(parts)


def run_drill(save_path=None):
    print("termcoach 编辑练习：三个任务，各做两遍（先自然习惯，再看建议重做）。")
    print("只编辑、不执行任何命令；输入不落盘。Ctrl-C 退出。\n")
    records = []
    for task in TASKS:
        print(f"━━ {task['id']} {task['title']} ━━")
        print(f"目标：{task['goal']}")
        # 基线遍
        print("\n[第 1 遍] 按你自然的习惯编辑，回车结束。")
        line1, s1 = run_edit(task["prefill"])
        ok1 = task["check"](line1) if line1 is not None else False
        print(f"  结果：{'✓ 正确' if ok1 else '✗ 未达目标'}｜{fmt_counts(s1)}")
        # 固定对照提示（第三问基线，与统计无关）
        print(f"  固定提示：{task['fixed_hint']}")
        # 统计驱动建议
        cs = coach_suggestion(task, s1)
        if cs:
            print(f"  教练建议：{cs}")
        else:
            print("  教练建议：（无——未观察到可改进的连续动作）")
        # 应用遍
        print("\n[第 2 遍] 同一任务，试着用刚才的快捷键重做。")
        line2, s2 = run_edit(task["prefill"])
        ok2 = task["check"](line2) if line2 is not None else False
        print(f"  结果：{'✓ 正确' if ok2 else '✗ 未达目标'}｜{fmt_counts(s2)}")
        # 对比
        if ok1 and ok2 and not s1["unsupported"] and not s2["unsupported"]:
            delta = s1["total_keys"] - s2["total_keys"]
            print(f"  对比：总键数 {s1['total_keys']} → {s2['total_keys']}（{'减少' if delta > 0 else '变化'} {abs(delta)}）")
        else:
            print("  对比：（两遍未都正确或含不支持输入，不计键数差）")
        records.append({
            "task": task["id"],
            "baseline": {k: s1[k] for k in ("total_keys", "backspace_runs", "arrow_runs", "ctrl_shortcuts", "unsupported")},
            "applied": {k: s2[k] for k in ("total_keys", "backspace_runs", "arrow_runs", "ctrl_shortcuts", "unsupported")},
            "baseline_ok": ok1,
            "applied_ok": ok2,
            "coach_hint_given": cs is not None,
        })
        print()
    # 汇总
    print("━━ 汇总 ━━")
    reduced = sum(1 for r in records
                  if r["baseline_ok"] and r["applied_ok"]
                  and not r["baseline"]["unsupported"] and not r["applied"]["unsupported"]
                  and r["applied"]["total_keys"] < r["baseline"]["total_keys"])
    print(f"验收问题1（键数减少的任务数）：{reduced}/3（需 ≥2，由你实操判定）")
    print("验收问题2（不支持输入沉默）：见各遍标注，单测已覆盖五类。")
    print("验收问题3（教练 vs 固定提示）：固定提示每遍都给；教练建议只在观察到对应动作时给。")
    print("  → 请判断：教练建议是否比固定提示更贴合你实际卡住的地方？")
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n已追加计数到 {save_path}（仅任务 ID + 动作计数，无命令/路径/按键原文）")
    return records


def main():
    ap = argparse.ArgumentParser(description="termcoach 编辑教练（受控练习，只编辑不执行）")
    ap.add_argument("--save", action="store_true",
                    help="追加纯计数 JSON 到 ~/.local/share/termcoach/drills.jsonl（默认不写、不建目录）")
    args = ap.parse_args()
    if not sys.stdin.isatty():
        print("需要在交互终端里运行。", file=sys.stderr)
        sys.exit(1)
    save_path = os.path.expanduser("~/.local/share/termcoach/drills.jsonl") if args.save else None
    try:
        run_drill(save_path)
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()