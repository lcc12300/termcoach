"""录制器核心：OSC 命令边界三段分离 + record on/off + 默认暂停 + 内存缓冲。

不依赖 history 1（P3/P5 验证）。命令文本=E→S 段输入；程序输入/输出=S→E 段。
首版无帧、无 pyte。录制内容只在内存，退出后预览确认才落盘。
"""
import re
import time

from .rec_decoder import InputDecoder, decode_command_text

OSC_RE = re.compile(rb"\x1b\]7770;coach;(S|E;(-?\d+)|ON|OFF)\x07")
# 完整标记的所有可能前缀（用于跨块缓冲判定）
_MARKER_FULL = [b"\x1b]7770;coach;S\x07", b"\x1b]7770;coach;ON\x07",
                b"\x1b]7770;coach;OFF\x07"] + \
               [f"\x1b]7770;coach;E;{n}\x07".encode() for n in (0, 1, 2, 127, 255)]
_MARKER_PREFIXES = set()
for _m in _MARKER_FULL:
    for _k in range(1, len(_m)):
        _MARKER_PREFIXES.add(_m[:_k])
# E; 后可跟任意整数，数字部分单独处理
_E_PREFIX = b"\x1b]7770;coach;E;"


def _partial_marker_len(tail: bytes) -> int:
    """tail 末尾最长可能是 coach 标记前缀的长度（0=无）。"""
    max_check = min(len(tail), 40)
    for k in range(max_check, 0, -1):
        suf = tail[-k:]
        if suf in _MARKER_PREFIXES:
            return k
        # E;数字 前缀（rc 可为任意整数，含负号）
        if _E_PREFIX.startswith(suf):
            return k
        if suf.startswith(_E_PREFIX):
            digits = suf[len(_E_PREFIX):]
            if digits in (b"-",) or digits.lstrip(b"-").isdigit():
                return k
    return 0


ERROR_RE = re.compile(r"error|Error|ERROR|fatal|undefined|No such|cannot|Cannot|E\d{2,3}:", re.I)

# 输出分段限量参数
HEAD_LINES = 8
TAIL_LINES = 4
ERR_CONTEXT = 2
MAX_OUTPUT_BYTES = 4096
# 采集期内存上限：超过就丢中段（否则长任务的输出会在内存里无限增长）。
# shortcut: 中段内容不参与复盘证据；需要完整输出请只录短段。
MAX_ACCUM_CHARS = 65536

# 全屏模式（vim/htop 等）检测：进入 alternate screen
ALT_SCREEN_ON = re.compile(r"\x1b\[\?(?:1049|47|1047)h")
ALT_SCREEN_OFF = re.compile(r"\x1b\[\?(?:1049|47|1047)l")
# vim 明文错误行（E21: / E45: / W10: 等），全屏输出里唯一可靠的可读证据
VIM_ERR_RE = re.compile(r"[EW]\d{2,3}: [^\x00-\x1f]{4,}")
# 全屏输出里值得保留的明文提示（目录浏览等）
NOTICE_RE = re.compile(r'"[^"\x00-\x1f]{2,80}" is a directory|Netrw Directory Listing')


def extract_screen_text(raw: str) -> str:
    """全屏程序输出 → 只提取明文错误/提示行，明确标注不是屏幕重建。

    不做通用 ANSI 屏幕还原（评审：删指令只会串起重绘碎片，首版无 pyte）。
    这里只捞 vim 自己打印的整句明文（E21/E45 等），它们是完整文本行，
    不依赖光标定位指令，提取是安全的。
    """
    notices = []
    seen = set()
    for m in VIM_ERR_RE.finditer(raw):
        s = m.group(0).strip()
        if s not in seen:
            seen.add(s)
            notices.append(s)
    for m in NOTICE_RE.finditer(raw):
        s = m.group(0).strip()
        if s not in seen:
            seen.add(s)
            notices.append(s)
    if not notices:
        return "[全屏程序输出，未提取到明文错误/提示；原始转义流已省略]"
    return "[全屏程序输出，仅提取明文错误/提示行，非屏幕重建]\n" + "\n".join(notices)


# 行式输出（非全屏）里可安全删除的纯装饰序列：SGR 颜色/属性、擦除到行尾。
# 不含光标定位——那类只在全屏分支处理，行式输出里出现则保留原样。
_DECOR_RE = re.compile(r"\x1b\[[0-9;]*m|\x1b\[K")


def _cap_bytes(text: str, limit: int) -> str:
    """按字节硬截，结果（含省略标记）不超过 limit。

    标记本身占字节，先按标记长度扣预算再分配首尾，否则会像 4122 > 4096 那样溢出。
    """
    b = text.encode("utf-8", "replace")
    if len(b) <= limit:
        return text
    marker = f"\n[已省略 {len(b)} 字节]\n"
    budget = limit - len(marker.encode("utf-8"))
    if budget < 64:                      # 上限太小：只留标记，别切出半个字
        return marker.lstrip("\n")[:limit]
    head_n = budget * 2 // 3
    tail_n = budget - head_n
    head = b[:head_n].decode("utf-8", "ignore")
    tail = b[len(b) - tail_n:].decode("utf-8", "ignore")
    return head + marker + tail


def trim_output(text: str) -> str:
    """保留开头 + 第一处错误附近 + 末尾，标明省略。先删纯装饰序列（颜色等）。

    最终结果有字节硬上限：行数少但单行超长（如一条 24KB 的 JSON）也截断，
    否则大输出会原样进入 transcript 和请求。
    """
    text = _DECOR_RE.sub("", text)
    if len(text.encode("utf-8", "replace")) <= MAX_OUTPUT_BYTES:
        return text
    lines = text.splitlines()
    keep_lines = HEAD_LINES + TAIL_LINES + 2 * ERR_CONTEXT + 2
    if len(lines) <= keep_lines:
        return _cap_bytes(text, MAX_OUTPUT_BYTES)
    head = lines[:HEAD_LINES]
    tail = lines[-TAIL_LINES:]
    err_idx = next((i for i, l in enumerate(lines) if ERROR_RE.search(l)), None)
    parts = ["\n".join(head), f"[省略 {len(lines) - HEAD_LINES - TAIL_LINES} 行]"]
    if err_idx is not None and HEAD_LINES <= err_idx < len(lines) - TAIL_LINES:
        lo = max(HEAD_LINES, err_idx - ERR_CONTEXT)
        hi = min(len(lines) - TAIL_LINES, err_idx + ERR_CONTEXT + 1)
        parts.append("[第一处错误附近]")
        parts.append("\n".join(lines[lo:hi]))
    parts.append("\n".join(tail))
    return _cap_bytes("\n".join(parts), MAX_OUTPUT_BYTES)


class Command:
    __slots__ = ("text", "prog_input", "output", "rc", "t_start", "boundary_known",
                 "fullscreen", "dropped")

    def __init__(self):
        self.text = None          # 命令文本（E→S 段），拿不到为 None=未知
        self.prog_input = ""      # 程序输入（S→E 段记号）
        self.output = ""          # 命令输出（S→E 段，剥 OSC 后）
        self.rc = None
        self.t_start = None
        self.boundary_known = True
        self.fullscreen = False   # 输出进过 alternate screen（vim/htop 等）
        self.dropped = 0          # 采集期因上限丢弃的字符数


class Recorder:
    """喂入 (kind, bytes)，kind='in'/'out'。维护录制状态与命令块。

    默认暂停。只有 RECORDING 期间的内容进缓冲。
    record on/off 由 shell 函数发 OSC ON/OFF 标记触发（输出流里识别）。
    """

    def __init__(self):
        self.recording = False
        self.commands = []        # 已完成的命令块
        self._cur = None          # 当前 S→E 段（程序运行中）
        self._prompt_input = InputDecoder()   # E→S 段输入解码（命令文本）
        self._prog_input = InputDecoder()     # S→E 段输入解码（程序输入）
        self._prompt_raw = ""     # 当前提示符编辑累积记号
        self._osc_pending = b""   # 跨块 OSC 标记缓冲
        self._t0 = time.monotonic()
        self._saw_s = False

    def _now(self):
        return round(time.monotonic() - self._t0, 2)

    def feed(self, kind: str, data: bytes):
        if kind == "in":
            self._feed_input(data)
        else:
            self._feed_output(data)

    def _feed_input(self, data: bytes):
        if not self.recording:
            return
        if self._cur is not None:
            # S→E 之间：程序输入
            self._cur.prog_input += self._prog_input.feed(data)
        else:
            # E→S 之间：提示符编辑（命令文本）
            self._prompt_raw += self._prompt_input.feed(data)

    def _feed_output(self, data: bytes):
        # 增量缓冲：OSC 标记可能被 PTY 拆到多个读取块，必须跨块拼合（P6 复现）
        data = self._osc_pending + data
        self._osc_pending = b""
        pos = 0
        for m in OSC_RE.finditer(data):
            self._out_segment(data[pos:m.start()])
            pos = m.end()
            tag = m.group(1)
            if tag == b"ON":
                self.recording = True
            elif tag == b"OFF":
                self.recording = False
            elif tag == b"S":
                self._on_s()
            elif tag.startswith(b"E;"):
                rc = int(m.group(2))
                self._on_e(rc)
        tail = data[pos:]
        # 尾部若可能是未完成的 coach 标记前缀，留到下一块；否则正常输出。
        # 只缓冲可能是标记前缀的尾巴，普通输出不受影响。
        hold = _partial_marker_len(tail)
        if hold:
            self._osc_pending = tail[-hold:]
            tail = tail[:-hold]
        self._out_segment(tail)

    def _out_segment(self, seg: bytes):
        if not seg or not self.recording:
            return
        if self._cur is not None:
            text = seg.decode("utf-8", "replace")
            if ALT_SCREEN_ON.search(text):
                self._cur.fullscreen = True
            self._accumulate(self._cur, text)

    @staticmethod
    def _accumulate(cmd, text: str):
        """把输出拼进缓冲，超过上限就丢中段（长任务不能撑爆内存）。"""
        cmd.output += text
        n = len(cmd.output)
        if n <= MAX_ACCUM_CHARS:
            return
        keep_head = MAX_ACCUM_CHARS // 3
        keep_tail = MAX_ACCUM_CHARS - keep_head
        cmd.dropped += n - MAX_ACCUM_CHARS
        cmd.output = cmd.output[:keep_head] + cmd.output[-keep_tail:]

    def _finalize_output(self, cmd):
        if cmd.fullscreen:
            cmd.output = extract_screen_text(cmd.output)
        else:
            cmd.output = trim_output(cmd.output)
        if cmd.dropped:
            cmd.output = (f"[会话输出过长，采集时已丢弃中段 {cmd.dropped} 字符]\n"
                          + cmd.output)

    def _on_s(self):
        # 命令开始：把累积的提示符编辑固化为命令文本
        if not self.recording:
            self._prompt_raw = ""
            self._prompt_input = InputDecoder()
            return
        cmd = Command()
        raw = self._prompt_raw + self._prompt_input.flush()
        cmd.text = decode_command_text(raw) if raw.strip() else None
        cmd.t_start = self._now()
        self._cur = cmd
        self._saw_s = True
        self._prompt_raw = ""
        self._prompt_input = InputDecoder()
        self._prog_input = InputDecoder()

    def _on_e(self, rc: int):
        if self._cur is not None:
            self._cur.rc = rc
            self._cur.prog_input += self._prog_input.flush()
            self._finalize_output(self._cur)
            self.commands.append(self._cur)
            self._cur = None
        # 空行：有 E 无 S，不产生块；重置提示符解码
        self._prompt_raw = ""
        self._prompt_input = InputDecoder()
        self._saw_s = False

    def finish(self):
        """会话结束：收尾未闭合段。"""
        if self._cur is not None:
            self._cur.prog_input += self._prog_input.flush()
            self._finalize_output(self._cur)
            self._cur.boundary_known = False  # 无 E，边界未知
            self.commands.append(self._cur)
            self._cur = None

    def render(self, include_keys: bool) -> str:
        """生成 transcript 文本。include_keys=False 为「仅命令+输出」基线版。"""
        lines = [f"# termcoach 录制", ""]
        n = 0
        for c in self.commands:
            # record on/off 自身不成块（控制命令，非用户工作）
            if c.text in ("record on", "record off"):
                continue
            n += 1
            rc = "?" if c.rc is None else c.rc
            text = c.text if c.text is not None else "（未知）"
            bnd = "" if c.boundary_known else "  [边界未知]"
            lines.append(f"## 命令 {n}  rc={rc}  t={c.t_start}s{bnd}")
            lines.append(f"命令: {text}")
            if include_keys and c.prog_input:
                lines.append(f"程序输入: {c.prog_input}")
            if c.output.strip():
                lines.append("输出:")
                lines.append(c.output.rstrip())
            lines.append("")
        return "\n".join(lines)