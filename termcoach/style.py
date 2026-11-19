"""终端配色：只在真终端上开，别把转义序列写进管道/日志。

开关顺序：TERMCOACH_COLOR=never/always > NO_COLOR > TERM=dumb > stdout 是否 TTY。
transcript 里不出现这些颜色：只给 termcoach 自己打印的提示上色，
录制内容与 POST 请求体都不经过这里。
"""
import os
import sys

RESET = "\x1b[0m"


def mode() -> str:
    return (os.environ.get("TERMCOACH_COLOR") or "auto").strip().lower()


def enabled(stream=None) -> bool:
    m = mode()
    if m == "never":
        return False
    if m == "always":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    s = sys.stdout if stream is None else stream
    return bool(getattr(s, "isatty", lambda: False)())


def paint(text: str, *codes: str, on=None) -> str:
    if not codes:
        return text
    if on is None:
        on = enabled()
    if not on:
        return text
    return "".join(f"\x1b[{c}m" for c in codes) + text + RESET


# 语义色板（层级从高到低：区块标题 → 条目名 → 数字 → 说明）
def title(t, on=None):
    return paint(t, "1;36", on=on)      # 粗体青：区块标题


def head(t, on=None):
    return paint(t, "1", on=on)         # 粗体：条目名/关键结论


def num(t, on=None):
    return paint(t, "1;33", on=on)      # 粗体黄：编号、计数


def accent(t, on=None):
    return paint(t, "36", on=on)        # 青：趋势数字、路径


def dim(t, on=None):
    return paint(t, "2", on=on)         # 暗：辅助说明、标签


def ok(t, on=None):
    return paint(t, "32", on=on)        # 绿：成功、导出路径


def warn(t, on=None):
    return paint(t, "1;31", on=on)      # 粗体红：警告


def key(t, on=None):
    return paint(t, "1;35", on=on)      # 粗体品红：要敲的命令/键


TREND_COLORS = {"improving": "32", "resolved": "32", "recurring": "33",
                "unknown": "2"}


def trend(value, on=None):
    return paint(value, TREND_COLORS.get(value, "2"), on=on)


def acceptance(value, on=None):
    return paint(f"{value:9s}".rstrip(), {"accepted": "32", "dismissed": "2",
                                         "proposed": "33"}.get(value, "2"), on=on)
