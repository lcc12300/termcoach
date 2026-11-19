"""按键动作计数器：只统计连续退格与连续左右移光标，其余一律沉默。

设计口径（cc 2026-11-18 确认）：
- 跨读取块但能正确拼出的方向键正常计数；只有无法识别的序列才沉默。
- bracketed paste 区间内的字节全部不计。
- Ctrl-R / 上下方向键（历史浏览）/ 未知 ESC 序列 → 整段编辑标 unsupported。
- 不保存按键原文；本模块只产出计数。
"""

BACKSPACE = frozenset((0x7F, 0x08))
ENTER = frozenset((0x0D, 0x0A))
LEFT = b"\x1b[D"
RIGHT = b"\x1b[C"
UP = b"\x1b[A"
DOWN = b"\x1b[B"
APP_LEFT = b"\x1bOD"
APP_RIGHT = b"\x1bOC"
APP_UP = b"\x1bOA"
APP_DOWN = b"\x1bOB"
PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"

# 已知的合法编辑快捷键：计数进 total，但不触发沉默
KNOWN_CTRL = frozenset((0x01, 0x02, 0x05, 0x06, 0x0B, 0x15, 0x17))  # A B E F K U W
KNOWN_ALT = frozenset((ord("b"), ord("f"), ord("d")))  # Alt-b/f/d

MAX_PENDING = 16  # 悬而未决的 ESC 缓冲超过此长度仍拼不出已知序列 → unsupported


def _lcp_len(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class KeyCounter:
    """喂入 PTY 读到的输入字节块（可任意切块），回车后取 summary()。"""

    def __init__(self):
        self.backspace_runs = []   # 每段连续退格的次数
        self.arrow_runs = []       # 每段连续左/右移的次数
        self.total_keys = 0        # 识别出的编辑动作总数（含快捷键，不含回车）
        self.ctrl_shortcuts = {}   # 如 {"C-a": 1, "M-b": 2}
        self.unsupported = False
        self.unsupported_reason = None
        self._pending = b""
        self._in_paste = False
        self._last_run = None      # "bs" / "arrow" / None，用于切段
        self._done = False

    # ---- 内部 ----
    def _mark_unsupported(self, reason):
        if not self.unsupported:
            self.unsupported = True
            self.unsupported_reason = reason

    def _break_run(self):
        self._last_run = None

    def _count_bs(self):
        self.total_keys += 1
        if self._last_run == "bs":
            self.backspace_runs[-1] += 1
        else:
            self.backspace_runs.append(1)
            self._last_run = "bs"

    def _count_arrow(self):
        self.total_keys += 1
        if self._last_run == "arrow":
            self.arrow_runs[-1] += 1
        else:
            self.arrow_runs.append(1)
            self._last_run = "arrow"

    def _count_shortcut(self, name):
        self.total_keys += 1
        self.ctrl_shortcuts[name] = self.ctrl_shortcuts.get(name, 0) + 1
        self._break_run()

    def _resolve_escape(self):
        """尝试把 _pending 解析成一个已知序列；返回 (consumed, kind) 或 None。

        kind: left/right/paste_start/paste_end/alt_b/alt_f/alt_d/unsupported
        需要更多字节时返回 None（保留 pending 等下一块）。
        """
        p = self._pending
        if not p:
            return None
        # 完整匹配优先
        for seq, kind in (
            (PASTE_START, "paste_start"), (PASTE_END, "paste_end"),
            (LEFT, "left"), (RIGHT, "right"),
            (APP_LEFT, "left"), (APP_RIGHT, "right"),
            (UP, "unsupported_up"), (DOWN, "unsupported_down"),
            (APP_UP, "unsupported_up"), (APP_DOWN, "unsupported_down"),
        ):
            if p.startswith(seq):
                return len(seq), kind
        if len(p) >= 2 and p[0] == 0x1B and p[1] in KNOWN_ALT:
            name = "M-" + chr(p[1])
            return 2, ("shortcut", name)
        # 是某个已知序列的前缀 → 等更多字节
        candidates = (PASTE_START, PASTE_END, LEFT, RIGHT, UP, DOWN,
                      APP_LEFT, APP_RIGHT, APP_UP, APP_DOWN)
        if any(c.startswith(p) for c in candidates):
            return None
        if len(p) == 1:  #  lone ESC，可能是 Alt 组合的开头
            return None
        if len(p) >= 2 and p[0] == 0x1B and p[1] not in (ord("["), ord("O")):
            # Alt+未知键：不评判，但也不沉默整段（不是历史浏览/未知 CSI）
            return 2, ("skip", None)
        # CSI/SS3 但匹配不上任何已知序列
        if len(p) > MAX_PENDING or (p[1] in (ord("["), ord("O")) and len(p) >= 3):
            return len(p), "unsupported_seq"
        return None

    # ---- 公开 ----
    def feed(self, chunk: bytes):
        if self._done or self.unsupported:
            # unsupported 后仍要消费到回车，避免污染下一段；但不再计数
            if self.unsupported and not self._done:
                for b in chunk:
                    if b in ENTER:
                        self._done = True
                        break
            return
        data = self._pending + chunk
        self._pending = b""
        i = 0
        while i < len(data):
            b = data[i]
            if self._in_paste:
                if data[i:].startswith(PASTE_END):
                    i += len(PASTE_END)
                    self._in_paste = False
                    self._break_run()
                    continue
                # 粘贴内容里出现回车也不结束编辑（readline 粘贴可含换行）
                i += 1
                continue
            if b in ENTER:
                self._done = True
                return
            if b in BACKSPACE:
                self._count_bs()
                i += 1
                continue
            if b == 0x12:  # Ctrl-R 历史搜索
                self._mark_unsupported("ctrl-r")
                return
            if b == 0x03:  # Ctrl-C
                self._mark_unsupported("ctrl-c")
                return
            if b == 0x1B:
                self._pending = data[i:]
                while True:
                    r = self._resolve_escape()
                    if r is None:
                        if len(self._pending) > MAX_PENDING:
                            self._mark_unsupported("unknown-sequence")
                            self._pending = b""
                            return
                        return  # 等下一块
                    consumed, kind = r
                    self._pending = self._pending[consumed:]
                    i += consumed
                    if kind in ("left", "right"):
                        self._count_arrow()
                    elif kind == "paste_start":
                        self._in_paste = True
                        self._break_run()
                    elif kind == "paste_end":
                        self._break_run()
                    elif isinstance(kind, tuple) and kind[0] == "shortcut":
                        self._count_shortcut(kind[1])
                    elif isinstance(kind, tuple) and kind[0] == "skip":
                        self._break_run()
                    elif kind in ("unsupported_up", "unsupported_down"):
                        self._mark_unsupported("history-browse")
                        return
                    elif kind == "unsupported_seq":
                        self._mark_unsupported("unknown-sequence")
                        return
                    if not self._pending:
                        break
                continue
            if b in KNOWN_CTRL:
                self._count_shortcut("C-" + chr(b + 96))
                i += 1
                continue
            if 0x20 <= b <= 0x7E:  # 可打印字符
                self.total_keys += 1
                self._break_run()
                i += 1
                continue
            # 其余控制字节（Tab 等）：不评判，打断连击
            self._break_run()
            i += 1

    def summary(self) -> dict:
        return {
            "backspace_runs": list(self.backspace_runs),
            "arrow_runs": list(self.arrow_runs),
            "total_keys": self.total_keys,
            "ctrl_shortcuts": dict(self.ctrl_shortcuts),
            "unsupported": self.unsupported,
            "unsupported_reason": self.unsupported_reason,
        }