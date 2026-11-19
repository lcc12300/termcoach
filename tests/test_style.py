"""配色：只在真终端开；不污染管道、日志和 transcript。"""
import os
import unittest
from unittest import mock

from termcoach import findings as F
from termcoach import style
from termcoach.metrics import key_metrics
from termcoach.recorder import Recorder


def _fake_tty(is_tty):
    m = mock.Mock()
    m.isatty.return_value = is_tty
    return m


class TestStyleGate(unittest.TestCase):
    def test_no_color_when_not_tty(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TERMCOACH_COLOR", None)
            os.environ.pop("NO_COLOR", None)
            self.assertFalse(style.enabled(_fake_tty(False)))
            self.assertEqual(style.title("x", on=False), "x")

    def test_no_color_env(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            self.assertFalse(style.enabled(_fake_tty(True)))

    def test_dumb_term(self):
        with mock.patch.dict(os.environ, {"TERM": "dumb"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertFalse(style.enabled(_fake_tty(True)))

    def test_explicit_always_wins(self):
        with mock.patch.dict(os.environ, {"TERMCOACH_COLOR": "always",
                                          "NO_COLOR": "1"}, clear=False):
            self.assertTrue(style.enabled(_fake_tty(False)))
            self.assertIn("\x1b[", style.title("x"))

    def test_explicit_never(self):
        with mock.patch.dict(os.environ, {"TERMCOACH_COLOR": "never"}, clear=False):
            self.assertFalse(style.enabled(_fake_tty(True)))


class TestTipsColoring(unittest.TestCase):
    def setUp(self):
        self.tips_data = [{
            "finding_id": "shell.readline.backspace_run", "title": "连续长退格",
            "acceptance": "accepted", "trend": "improving", "times_seen": 2,
            "recommendation": "Ctrl-U 清行", "verification": "不超过 2 连",
            "history": [{"evidence": {"program.max_backspace_run": 34}},
                        {"evidence": {"program.max_backspace_run": 2}}],
        }]

    def test_plain_still_used_when_off(self):
        out = F.format_tips(self.tips_data, on=False)
        self.assertNotIn("\x1b[", out)
        self.assertIn("已被提出 2 次", out)
        self.assertIn("program.max_backspace_run: 34 → 2", out)

    def test_hierarchy_present_when_on(self):
        out = F.format_tips(self.tips_data, on=True)
        self.assertIn("\x1b[", out)
        # 纯文本内容不能被颜色拆散（仍可按整句检索/断言）
        self.assertIn("已被提出 2 次", out)
        self.assertIn("34 → 2", out)
        # 至少三层不同颜色：标题青、数字黄、说明暗
        self.assertIn("1;36", out)
        self.assertIn("1;33", out)
        self.assertIn("\x1b[2m", out)

    def test_trend_and_acceptance_vocab(self):
        self.assertEqual(style.trend("improving", on=True), "\x1b[32mimproving\x1b[0m")
        self.assertEqual(style.trend("recurring", on=True), "\x1b[33mrecurring\x1b[0m")
        self.assertEqual(style.acceptance("dismissed", on=True), "\x1b[2mdismissed\x1b[0m")


class TestTranscriptStaysPlain(unittest.TestCase):
    def test_colored_output_and_record_block_not_exported(self):
        """着色回显会进会话输出，但 render 必须剥离 SGR；record on/off 块整块跳过。"""
        r = Recorder()
        r.feed("out", b"\x1b]7770;coach;ON\x07")
        # 第一条：record off 自己的彩色回显 → 该块整块跳过
        r.feed("in", b"record off\r")
        r.feed("out", b"\x1b]7770;coach;S\x07")
        r.feed("out", b"\x1b[1;33m[termcoach] \xe5\xbd\x95\xe5\x88\xb6\xe6\x9a\x82\xe5\x81\x9c\x1b[0m\n")
        r.feed("out", b"\x1b]7770;coach;E;0\x07")
        # 第二条：普通命令，输出里也带颜色
        r.feed("out", b"\x1b]7770;coach;ON\x07")
        r.feed("in", b"cat log\r")
        r.feed("out", b"\x1b]7770;coach;S\x07")
        r.feed("out", b"\x1b[1;32mok\x1b[0m done\n")
        r.feed("out", b"\x1b]7770;coach;E;0\x07")
        text = r.render(include_keys=True)
        self.assertNotIn("\x1b[", text)
        self.assertIn("cat log", text)
        self.assertNotIn("record off", text)      # 控制块被跳过
        self.assertEqual(key_metrics(text)["command_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)