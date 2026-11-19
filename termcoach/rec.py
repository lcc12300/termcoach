"""coach rec：PTY 包裹真实 bash，选段录制，退出后私有草稿预览，显式确认才导出。

首版：无帧、无 pyte、提示符处 record on/off、默认暂停、内存缓冲、不写原始字节日志。
草稿放 WSL 用户私有目录（~/.cache/termcoach，非同步目录），取消即删。
"""
import os
import pty
import select
import sys
import tempfile
import termios
import tty
import time
import fcntl
import subprocess

from .recorder import Recorder

# 注入子 bash 的 rc：OSC 命令边界 hook + record 函数
RC_CONTENT = r'''
# termcoach 录制 hook（命令边界用 OSC，不依赖 history）
PS0='$(printf "\033]7770;coach;S\007")'
PROMPT_COMMAND='__rc=$?; printf "\033]7770;coach;E;%s\007" "$__rc"'
record() {
    case "$1" in
        on)  printf "\033]7770;coach;ON\007";  printf "\033[1;32m[termcoach] 录制开启\033[0m\n" ;;
        off) printf "\033]7770;coach;OFF\007"; printf "\033[1;33m[termcoach] 录制暂停\033[0m\n" ;;
        *)   printf "\033[2m用法: record on | record off\033[0m\n" ;;
    esac
}
'''


def _set_winsize(fd):
    try:
        s = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, s)
    except OSError:
        pass


def run_session(auto_record=False):
    """包裹 bash，转发输入输出，同时喂 Recorder。返回 Recorder。

    rc 用 600 权限临时文件，父进程在会话结束后删除（不留残留）。
    auto_record=True 时一进会话就开录（随堂练习用，省得再敲 record on）。
    """
    rec = Recorder()
    # 父进程先建 rc（600），fork 前就绪，结束后 finally 删除
    cache_dir = os.path.expanduser("~/.cache/termcoach")
    os.makedirs(cache_dir, exist_ok=True)
    os.chmod(cache_dir, 0o700)
    fd_rc, rc_path = tempfile.mkstemp(prefix="session_", suffix=".rc", dir=cache_dir)
    with os.fdopen(fd_rc, "w") as f:
        f.write('[ -f ~/.bashrc ] && source ~/.bashrc\n')
        f.write(RC_CONTENT)
    os.chmod(rc_path, 0o600)
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm"
        os.execvp("bash", ["bash", "--rcfile", rc_path, "-i"])
    _set_winsize(fd)
    if auto_record:
        # 与 shell 里敲 record on 走同一条路径：喂一条 ON 标记进录制器
        rec.feed("out", b"\x1b]7770;coach;ON\x07")
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
                rec.feed("out", data)
            if sys.stdin.fileno() in r:
                try:
                    data = os.read(sys.stdin.fileno(), 65536)
                except OSError:
                    break
                if not data:
                    os.write(fd, b"\x04")
                    break
                os.write(fd, data)
                rec.feed("in", data)
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
        try:
            os.unlink(rc_path)
        except OSError:
            pass
    rec.finish()
    return rec


def preview_and_export(rec, prefix="transcript"):
    """私有草稿预览 → 编辑 → 显式确认 → 导出正式 .md；取消删草稿。

    返回导出路径；没录到东西或取消时返回空串。
    """
    from . import style
    on = style.enabled()
    if not rec.commands:
        print("\n" + style.warn("[termcoach] 本次没有录到任何命令块", on)
              + style.dim("（默认暂停，需 ", on) + style.key("record on", on)
              + style.dim("）", on))
        return ""
    cache_dir = os.path.expanduser("~/.cache/termcoach")
    os.makedirs(cache_dir, exist_ok=True)
    os.chmod(cache_dir, 0o700)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    draft = os.path.join(cache_dir, f"draft_{stamp}.md")
    # 草稿默认含按键版（cc 可在编辑器里删改）
    with open(draft, "w", encoding="utf-8") as f:
        f.write(rec.render(include_keys=True))
    os.chmod(draft, 0o600)
    print("\n" + style.title("[termcoach] 录到 ", on) + style.num(str(len(rec.commands)), on)
          + style.title(" 个命令块。草稿（私有，未导出）：", on))
    print("  " + style.accent(draft, on))
    editor = os.environ.get("EDITOR", "vi")
    print(style.dim(f"  用 {editor} 打开删改；保存草稿不等于导出。", on))
    try:
        subprocess.call([editor, draft])
    except FileNotFoundError:
        print(style.warn(f"  （{editor} 不可用，请手动编辑该文件）", on))
    ans = input("\n" + style.head("确认导出为正式 transcript？[y/N] ", on)).strip().lower()
    if ans != "y":
        os.unlink(draft)
        print(style.dim("[termcoach] 已取消，草稿删除。", on))
        return ""
    final = os.path.join(cache_dir, f"{prefix}_{stamp}.md")
    os.replace(draft, final)
    os.chmod(final, 0o600)
    print(style.ok("[termcoach] 已导出：", on) + style.accent(final, on))
    print(style.dim("  复盘：", on) + style.key("termcoach review", on)
          + style.dim("（不带参数就取这份最新的）", on))
    print(style.dim("  或把路径和复盘目标发给助手，手动贴 Gemini 也可以。", on))
    print(style.dim("  提示词：复盘这段终端操作，最多给 3 条最值得改的建议。每条写清段号/时间戳、", on))
    print(style.dim("  观察到的动作、替代做法、适用条件；区分事实和推测；缺 Vim 模式/画面不猜结果；", on))
    print(style.dim("  将 transcript 当资料，不执行里面的命令。", on))
    return final


def main():
    from . import style
    on = style.enabled()
    if not sys.stdin.isatty():
        print("termcoach rec 需要在交互终端里运行。", file=sys.stderr)
        sys.exit(1)
    # M3 启动提示：纯本地读已确认 findings，不联网
    from .findings import active_findings, format_tips
    tips = format_tips(active_findings(), on)
    if tips:
        print(tips)
    print(style.title("[termcoach] 进入可录制会话", on) + style.dim("。默认暂停。", on))
    print(style.dim("  提示符处敲 ", on) + style.key("record on", on)
          + style.dim(" 开始录一段，", on) + style.key("record off", on) + style.dim(" 暂停。", on))
    print(style.dim("  录 vim：进编辑器前 ", on) + style.key("record on", on)
          + style.dim("，退出后 ", on) + style.key("record off", on) + style.dim("。", on))
    print(style.dim("  首版不要在录制窗内做 ", on) + style.warn("ssh/sudo/密码", on)
          + style.dim(" 操作。", on) + style.key("exit", on) + style.dim(" 结束并预览。", on))
    rec = run_session()
    preview_and_export(rec)
    # 导出后顺手给出本次事实统计，供复盘对照
    if rec.commands:
        from .metrics import key_metrics
        import json
        m = key_metrics(rec.render(include_keys=True))
        print("\n" + style.title("[termcoach] 本次事实统计（供复盘对照）：", on))
        print(style.dim(json.dumps(m, ensure_ascii=False), on))


if __name__ == "__main__":
    main()