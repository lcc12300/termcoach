"""确定性事实统计：只算事实，不给建议（评审 §三）。

从 transcript 文本（render 产物）提取可复核的计数，供 findings 记忆层
做跨会话趋势比较（35 → 12 → 2）。不保存命令原文/路径/输出。

三条口径（2026-11-18 评审修正）：
1. 粘贴正文不是按键。`<paste>…</paste>` 里的字面量 `<BS>` 只是文本，
   统计前整段隔离，只计粘贴次数与正文长度（不含标签）。
2. 提示符编辑与前台程序输入分开统计，连击不跨通道、不跨命令。
3. 段落按行解析，粘贴正文里的 `输出:` / `## 命令` 不算段落边界
   （否则多行粘贴会提前结束统计，粘贴之后的真实按键全被丢掉）。
"""
import math
import re

# 记号（与 rec_decoder 输出一致）
_BS = "<BS>"
_LEFT = "<Left>"
_RIGHT = "<Right>"
_UP = "<Up>"
_DOWN = "<Down>"
_CTRL = re.compile(r"<(C-[a-z@\\\]\^_])>")
_ALT = re.compile(r"<(M-[a-zA-Z])>")

_BLOCK_HEAD = re.compile(r"^## 命令 \d+\s+rc=(\S+)\s+t=[\d.]+s")
# 整段粘贴：开标记（含未闭合变体）+ 正文 + 结束标记（可能缺失）
_PASTE_SPAN = re.compile(r"<paste(?:…未闭合)?>(?P<body>.*?)(?:</paste>|\Z)", re.S)
_PASTE_SEP = "<PB>"          # 隔离记号：不计数，也断开两侧连击

_COUNT_KEYS = ("backspace_total", "left_right_total", "up_down_total",
               "paste_count", "paste_chars")
_NUMERIC_LEAF = ("backspace_total", "max_backspace_run", "left_right_total",
                 "max_left_right_run", "up_down_total", "paste_count", "paste_chars")

# evidence 只接受这些键（AI 不能自造统计名），值由程序按本次统计填入
KNOWN_METRIC_KEYS = frozenset(
    _NUMERIC_LEAF + ("command_count", "failed_command_count")
) | frozenset(f"{ch}.{k}" for ch in ("prompt", "program") for k in _NUMERIC_LEAF)


def metric_value(metrics: dict, key: str):
    """按点分键从 metrics 取值；未知键返回 None。"""
    if not isinstance(metrics, dict):
        return None
    cur = metrics
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None


def _max_run(tokens, kind):
    best = cur = 0
    for t in tokens:
        if t == kind:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _tokenize(s):
    """把记号串切成 token 列表：<BS> 等记号一个 token，可打印字符各一个。"""
    out = []
    i = 0
    while i < len(s):
        if s[i] == "<":
            j = s.find(">", i)
            if j != -1 and j - i <= 12:
                out.append(s[i:j+1])
                i = j + 1
                continue
        out.append(s[i])
        i += 1
    return out


def strip_paste(s: str) -> str:
    """把粘贴正文整段替换成隔离记号（保留开/闭标记位置信息之外的正文）。"""
    return _PASTE_SPAN.sub(_PASTE_SEP, s)


def paste_stats(s: str):
    """(粘贴次数, 正文长度)：正文长度不含 <paste>/</paste> 标签。"""
    n = chars = 0
    for m in _PASTE_SPAN.finditer(s):
        n += 1
        chars += len(m.group("body"))
    return n, chars


def _channel_stats(s):
    """单通道、单命令的统计。s 是该通道的记号串（可能含粘贴）。"""
    body = strip_paste(s)
    toks = _tokenize(body)
    ctrl, alt = {}, {}
    for m in _CTRL.finditer(body):
        ctrl[m.group(1)] = ctrl.get(m.group(1), 0) + 1
    for m in _ALT.finditer(body):
        alt[m.group(1)] = alt.get(m.group(1), 0) + 1
    pcount, pchars = paste_stats(s)
    return {
        "backspace_total": toks.count(_BS),
        "max_backspace_run": _max_run(toks, _BS),
        "left_right_total": toks.count(_LEFT) + toks.count(_RIGHT),
        "max_left_right_run": max(_max_run(toks, _LEFT), _max_run(toks, _RIGHT)),
        "up_down_total": toks.count(_UP) + toks.count(_DOWN),
        "ctrl_shortcut_counts": ctrl,
        "alt_shortcut_counts": alt,
        "paste_count": pcount,
        "paste_chars": pchars,
    }


def _empty():
    d = {k: 0 for k in _COUNT_KEYS}
    d.update({"max_backspace_run": 0, "max_left_right_run": 0,
              "ctrl_shortcut_counts": {}, "alt_shortcut_counts": {}})
    return d


def _merge(total, one):
    """合并多条命令：计数相加，连击取最大（不跨命令）。"""
    for k in _COUNT_KEYS:
        total[k] += one[k]
    for k in ("max_backspace_run", "max_left_right_run"):
        total[k] = max(total[k], one[k])
    for k in ("ctrl_shortcut_counts", "alt_shortcut_counts"):
        for name, n in one[k].items():
            total[k][name] = total[k].get(name, 0) + n
    return total


def iter_blocks(text: str):
    """逐行解析 render 产物 → [{rc, prompt, program}]。

    粘贴状态跨行跟踪：正文里以 `输出:` 或 `## 命令` 开头的行只是文本，
    不能当成段落边界；否则多行粘贴会提前结束，后面的真实按键被丢掉。
    """
    blocks = []
    cur = None
    channel = None
    in_paste = False
    for line in text.splitlines():
        opens = line.count("<paste")
        closes = line.count("</paste>")
        m = None if in_paste else _BLOCK_HEAD.match(line)
        if m:
            cur = {"rc": m.group(1), "prompt": "", "program": ""}
            blocks.append(cur)
            channel = None
        elif cur is not None:
            if in_paste:
                if channel == "program":
                    cur["program"] += "\n" + line
            elif line.startswith("命令: "):
                cur["prompt"] += line[len("命令: "):]
                channel = "prompt"
            elif line.startswith("程序输入: "):
                cur["program"] += line[len("程序输入: "):]
                channel = "program"
            elif line.startswith("输出:"):
                channel = None
            elif channel == "program":
                cur["program"] += "\n" + line
        if opens and not in_paste:
            in_paste = True
        if closes:
            in_paste = False
    return blocks


def key_metrics(transcript: str) -> dict:
    """从 transcript 提取事实计数。输入是 render(include_keys=True) 的文本。

    返回 prompt（提示符编辑）/ program（前台程序输入）两组通道统计，
    外加两者汇总的顶层同名键（保持旧调用方与 findings 历史兼容）。
    顶层 max_* 是「各命令各通道的最大值」，不是全篇连起来算的。
    """
    blocks = iter_blocks(transcript)
    prompt_total, prog_total = _empty(), _empty()
    for b in blocks:
        if b["prompt"]:
            _merge(prompt_total, _channel_stats(b["prompt"]))
        if b["program"]:
            _merge(prog_total, _channel_stats(b["program"]))

    total = _merge(_merge(_empty(), prompt_total), prog_total)
    out = {
        "command_count": len(blocks),
        "failed_command_count": sum(1 for b in blocks if b["rc"] not in ("0", "?")),
        "prompt": prompt_total,
        "program": prog_total,
    }
    out.update(total)
    return out


def is_finite_number(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))
