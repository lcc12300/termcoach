"""随堂练习：把这次发现的问题变成一个小任务，在终端里做一遍。

边界（决定这个模块能不能存在的前提）：
- 练习内容由模型写，**程序从不执行任何命令**，全部是给人看的文字。
- 练习在程序建的空目录里做，不碰用户真实文件；默认开录制，退出就能复盘。
- 练习文本先过一遍明显危险命令的粗筛。这不是安全边界（你自己在 shell 里
  敲什么，工具拦不住），只是不让模型把 `sudo`/`rm -rf` 当成练习题。
"""
import os
import re
import time

from .rec import preview_and_export, run_session

# 练习目录的固定小样：路径够长，能练退格/Tab 补全/行首编辑
FIXTURE = {
    "README.md": "# 练习目录\n\n本目录只用于随堂练习，文件可以随意改、删、重建。\n",
    "report_2026_final_draft_v3.txt": "draft content\n",
    "notes.md": "todo: 记下刚才那条建议\n",
    os.path.join("src", "main.c"): "#include <stdio.h>\nint main(void) {\n    return 0;\n}\n",
}

# 粗筛：命中的练习直接不提供。shortcut: 词面匹配，绕过方式很多，不当安全边界。
_DANGER = re.compile(
    r"\b(sudo|su\s|ssh\s|scp\s|rsync\s|mkfs|dd\s|shutdown|reboot|init\s0|"
    r"apt(-get)?\s+install|pip\s+install|curl\s|wget\s|nc\s|ncat\s|"
    r"chown\s|chmod\s+777|rm\s+-[rf]|>\s*/dev/|:\s*\(\)\s*\{)",
    re.I,
)


def looks_dangerous(*texts) -> str:
    """返回命中的词（没命中返回空串）。"""
    for t in texts:
        m = _DANGER.search(t or "")
        if m:
            return m.group(0).strip()
    return ""


def provision(workspace: str) -> None:
    """建练习目录和固定小样（不覆盖已存在的内容）。"""
    os.makedirs(workspace, exist_ok=True)
    os.chmod(workspace, 0o700)
    for rel, content in FIXTURE.items():
        p = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)


def render_brief(practice: dict, finding_title: str = "", workspace: str = "") -> str:
    from . import style
    on = style.enabled()
    lines = [style.title("[termcoach] 随堂练习", on)
             + style.dim("（不想练就回车跳过，不影响记忆）", on)]
    if finding_title:
        lines.append("  " + style.dim("针对: ", on) + style.head(finding_title, on))
    lines.append("  " + style.dim("任务: ", on) + practice.get("task", ""))
    steps = practice.get("steps") or []
    if steps:
        lines.append("  " + style.dim("步骤:", on))
        for i, s in enumerate(steps, 1):
            lines.append(f"    {style.num(f'{i}.', on)} {s}")
    if practice.get("done_when"):
        lines.append("  " + style.dim("做完的标准: ", on) + practice["done_when"])
    mins = practice.get("minutes")
    tail = "录制定在开启状态，退出后可以直接复盘"
    if mins:
        tail = f"预计 {mins} 分钟 · " + tail
    lines.append("  " + style.dim(tail, on))
    if workspace:
        lines.append("  " + style.dim("工作目录: ", on) + style.accent(workspace, on)
                     + style.dim("（文件随便改）", on))
    return "\n".join(lines)


def start(practice: dict, finding_title: str = "") -> str:
    """跑一遍练习并导出 transcript；返回路径（没录到东西返回空串）。"""
    from . import style
    guard = looks_dangerous(practice.get("task", ""), practice.get("done_when", ""),
                            *(practice.get("steps") or []))
    if guard:
        print(style.warn(f"[termcoach] 这条练习含危险命令（{guard}），已跳过。",
                         style.enabled()))
        return ""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    workspace = os.path.expanduser(f"~/.cache/termcoach/practice/practice_{stamp}")
    provision(workspace)
    print("\n" + render_brief(practice, finding_title, workspace))
    input(style.dim("回车开始练习（Ctrl-C 放弃）…", style.enabled()))
    old_cwd = os.getcwd()
    try:
        os.chdir(workspace)
        rec = run_session(auto_record=True)
    finally:
        os.chdir(old_cwd)
    return preview_and_export(rec, prefix="practice")
