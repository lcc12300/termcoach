"""录制器输入解码器：把输入字节流转成可读按键记号，跨回车持续解码。

与练习器 counter.py 完全独立（评审要求不共用）。
只解码记号，不判断 vim 模式/语义——那是 AI 的事。
"""

# 控制字节 → 记号
CTRL = {
    0x00: "<C-@>", 0x01: "<C-a>", 0x02: "<C-b>", 0x03: "<C-c>", 0x04: "<C-d>",
    0x05: "<C-e>", 0x06: "<C-f>", 0x07: "<C-g>", 0x08: "<BS>", 0x09: "<Tab>",
    0x0A: "<NL>", 0x0B: "<C-k>", 0x0C: "<C-l>", 0x0D: "<CR>", 0x0E: "<C-n>",
    0x0F: "<C-o>", 0x10: "<C-p>", 0x11: "<C-q>", 0x12: "<C-r>", 0x13: "<C-s>",
    0x14: "<C-t>", 0x15: "<C-u>", 0x16: "<C-v>", 0x17: "<C-w>", 0x18: "<C-x>",
    0x19: "<C-y>", 0x1A: "<C-z>", 0x1C: "<C-\\>", 0x1D: "<C-]>", 0x1E: "<C-^>",
    0x1F: "<C-_>", 0x7F: "<BS>",
}

# ESC 序列 → 记号（CSI/SS3）
ESC_SEQ = {
    b"\x1b[A": "<Up>", b"\x1b[B": "<Down>", b"\x1b[C": "<Right>", b"\x1b[D": "<Left>",
    b"\x1bOA": "<Up>", b"\x1bOB": "<Down>", b"\x1bOC": "<Right>", b"\x1bOD": "<Left>",
    b"\x1b[H": "<Home>", b"\x1b[F": "<End>", b"\x1bOH": "<Home>", b"\x1bOF": "<End>",
    b"\x1b[2~": "<Insert>", b"\x1b[3~": "<Delete>", b"\x1b[5~": "<PageUp>",
    b"\x1b[6~": "<PageDown>", b"\x1b[1~": "<Home>", b"\x1b[4~": "<End>",
    b"\x1b[Z": "<S-Tab>",
}
PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"
MAX_PENDING = 64

# SGR 鼠标上报：ESC [ < Ps;Ps;Ps M/m —— 终端自动产生，不是按键，静默丢弃
import re as _re
_MOUSE_RE = _re.compile(rb"^\x1b\[<\d+;\d+;\d+[Mm]")
# 终端查询响应（输入流里出现 = 终端自动应答，不是用户按键）：
#   CSI Ps;Ps R  (光标位置报告 CPR)   CSI ? Ps;... c (设备属性)   CSI ? ... $ y (DECRQM)
_RESP_RE = _re.compile(rb"^\x1b\[[?>]?[\d;]*[Rc]|\x1b\[\?[\d;]*\$y")
# OSC 响应（终端配色/光标颜色查询应答）：ESC ] ... BEL 或 ESC \ 结尾
_OSC_RESP_RE = _re.compile(rb"^\x1b\][^\x07\x1b]*(\x07|\x1b\\)")
# 焦点事件上报：ESC[I (focus in) / ESC[O (focus out)，vim 开 1004h 后终端自动发
_FOCUS_RE = _re.compile(rb"^\x1b\[[IO]")


def _is_prefix_of_known(p: bytes) -> bool:
    for seq in ESC_SEQ:
        if seq.startswith(p):
            return True
    for seq in (PASTE_START, PASTE_END):
        if seq.startswith(p):
            return True
    # Alt+字母 形式 ESC x
    if len(p) == 1 and p == b"\x1b":
        return True
    return False


class InputDecoder:
    """喂入输入字节块，产出记号字符串。跨块、跨回车持续工作。"""

    def __init__(self):
        self._pending = b""
        self._in_paste = False
        self._paste_buf = bytearray()

    def feed(self, chunk: bytes) -> str:
        data = self._pending + chunk
        self._pending = b""
        out = []
        i = 0
        while i < len(data):
            b = data[i]
            if self._in_paste:
                rem = data[i:]
                if rem.startswith(PASTE_END):
                    out.append(self._paste_buf.decode("utf-8", "replace"))
                    out.append("</paste>")
                    self._paste_buf = bytearray()
                    self._in_paste = False
                    i += len(PASTE_END)
                    continue
                # PTY 可能把结束标记拆到两次读取（如 \x1b[20 + 1~）：
                # 剩余字节是标记前缀就先留着等下一块，不能当正文吞掉
                if len(rem) < len(PASTE_END) and PASTE_END.startswith(rem):
                    self._pending = rem
                    break
                self._paste_buf.append(b)
                i += 1
                continue
            if b == 0x1B:
                self._pending = data[i:]
                consumed = 0
                while self._pending and self._pending[0] == 0x1B:
                    r = self._resolve(out)
                    if r is None:
                        if len(self._pending) > MAX_PENDING:
                            out.append("<Esc?>")
                            self._pending = b""
                            break
                        return "".join(out)  # 等下一块
                    if r == 0:
                        break
                    consumed += r
                i += consumed
                # 清空 _pending：剩余非 ESC 字节本就在 data[i:] 里，主循环会继续处理，
                # 留在 _pending 会在下一块被重复前置
                self._pending = b""
                continue
            if b in CTRL:
                out.append(CTRL[b])
                i += 1
                continue
            if 0x20 <= b <= 0x7E:
                out.append(chr(b))
                i += 1
                continue
            # 其他字节：原样转义记号
            out.append(f"<0x{b:02x}>")
            i += 1
        return "".join(out)

    def _resolve(self, out):
        p = self._pending
        if not p:
            return 0
        # 终端自动上报（鼠标/查询响应/OSC 应答）：不是用户按键，静默丢弃
        for rx in (_MOUSE_RE, _RESP_RE, _OSC_RESP_RE, _FOCUS_RE):
            m = rx.match(p)
            if m:
                self._pending = p[m.end():]
                return m.end()
        # 可能是上报序列的前缀（跨块）：等更多字节，别当未知序列吐出去
        if self._maybe_report_prefix(p):
            return None
        if p.startswith(PASTE_START):
            out.append("<paste>")
            self._in_paste = True
            self._pending = p[len(PASTE_START):]
            return len(PASTE_START)
        for seq, name in ESC_SEQ.items():
            if p.startswith(seq):
                out.append(name)
                self._pending = p[len(seq):]
                return len(seq)
        # lone ESC：不猜 Alt，等下一字节再定（跨块安全）
        if len(p) == 1:
            return None
        # Alt+字母（ESC 后已有字节）；ESC+非字母按 <Esc>+字面量（vim 习惯，不猜 Alt）
        if len(p) >= 2 and p[0] == 0x1B and chr(p[1]).isalpha():
            out.append(f"<M-{chr(p[1])}>")
            self._pending = p[2:]
            return 2
        if _is_prefix_of_known(p):
            return None  # 等更多
        # 未知 ESC 序列：标未知，不猜
        if len(p) >= 2 and p[1] in (ord("["), ord("O")):
            # CSI/SS3 但匹配不上：吃到非参数字节为止
            j = 2
            while j < len(p) and (0x30 <= p[j] <= 0x3F):
                j += 1
            if j < len(p):
                out.append("<Esc?>" + p[2:j+1].decode("latin1"))
                self._pending = p[j+1:]
                return j + 1
            return None
        # lone ESC 后跟非 CSI/SS3：当 <Esc>
        out.append("<Esc>")
        self._pending = p[1:]
        return 1

    @staticmethod
    def _maybe_report_prefix(p: bytes) -> bool:
        """p 是否可能是鼠标/查询/OSC 上报的不完整前缀（等下一块）。"""
        if p in (b"\x1b", b"\x1b[", b"\x1b[<", b"\x1b[?", b"\x1b[>", b"\x1b]"):
            return True
        if p.startswith(b"\x1b[<"):
            return _re.fullmatch(rb"\x1b\[<[\d;]*", p) is not None
        if p.startswith(b"\x1b["):
            # CSI 数字/分号参数，尚无终结符；含 ?/> 前缀或 $ 中间符也等
            return _re.fullmatch(rb"\x1b\[[?>]?[\d;]*\$?", p) is not None
        if p.startswith(b"\x1b]"):
            # OSC 尚无 BEL/ST 终结
            return b"\x07" not in p and b"\x1b\\" not in p[2:]
        return False

    def flush(self) -> str:
        """会话结束，吐出残留 pending。"""
        out = []
        if self._in_paste:
            out.append("<paste…未闭合>")
        if self._pending:
            out.append("<Esc?>" + self._pending.decode("latin1"))
            self._pending = b""
        return "".join(out)


def decode_command_text(raw: str) -> str:
    """从提示符编辑记号里提取命令文本：去掉结尾 <CR>/<NL>，其余保留。

    含编辑键（<BS>/<Left> 等）时不重建最终文本（评审：不猜），
    返回原始记号串并标注含编辑；纯可打印+回车则返回干净文本。
    """
    s = raw
    while s.endswith("<CR>") or s.endswith("<NL>"):
        s = s[:-4]
    return s