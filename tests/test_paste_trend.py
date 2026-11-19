"""第二批评审的两条硬回归：粘贴边界不能吞按键、跨通道数字不能串成趋势。

复审时用它们做准入门槛：不合格就不该发版。
"""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from termcoach import findings as F
from termcoach import llm
from termcoach.metrics import key_metrics
from termcoach.rec_decoder import PASTE_END, PASTE_START, InputDecoder
from termcoach.recorder import Recorder


# ---------- 高1：多行粘贴吞掉真实按键 ----------

class TestPasteBoundary(unittest.TestCase):
    def test_paste_line_starting_with_output_marker(self):
        """粘贴正文里有一行以 `输出:` 开头，统计不能提前结束。"""
        t = (
            "## 命令 1  rc=0  t=1.0s\n"
            "命令: cat > notes.md\n"
            "程序输入: <paste>第一行\n"
            "输出: 这行只是粘贴内容，不是段落边界\n"
            "第三行</paste>"
            "<BS><BS><BS>"          # 粘贴之后的真实按键，必须计到
            "<Esc>:wq<CR>\n"
            "输出:\n"
            "done\n"
        )
        m = key_metrics(t)
        self.assertEqual(m["program"]["backspace_total"], 3, m)
        self.assertEqual(m["program"]["max_backspace_run"], 3, m)
        self.assertEqual(m["program"]["paste_count"], 1)
        self.assertEqual(m["command_count"], 1)

    def test_paste_line_starting_with_block_header(self):
        """粘贴正文里出现 `## 命令` 也不能凭空多出一个命令块。"""
        t = (
            "## 命令 1  rc=0  t=1.0s\n"
            "命令: cat\n"
            "程序输入: <paste>\n"
            "## 命令 2  rc=1  t=9.9s\n"
            "正文</paste><BS><BS>\n"
        )
        m = key_metrics(t)
        self.assertEqual(m["command_count"], 1, m)
        self.assertEqual(m["failed_command_count"], 0, m)
        self.assertEqual(m["program"]["backspace_total"], 2, m)

    def test_paste_chars_excludes_tags(self):
        t = "## 命令 1  rc=0  t=1.0s\n命令: cat\n程序输入: <paste>abc</paste>\n"
        m = key_metrics(t)
        self.assertEqual(m["program"]["paste_chars"], 3, m)   # 不是 18

    def test_decoder_handles_split_paste_end(self):
        """PTY 把结束标记拆成两次读取，结束标记不能丢，之后的按键要计数。"""
        d = InputDecoder()
        out = d.feed(PASTE_START + b"body")
        split = len(PASTE_END) - 2
        out += d.feed(PASTE_END[:split])       # 前 4 字节
        out += d.feed(PASTE_END[split:] + b"\x7f\x7f\x7f")
        out += d.flush()
        self.assertIn("</paste>", out, out)
        self.assertEqual(out.count("<BS>"), 3, out)
        self.assertNotIn("未闭合", out)

    def test_recorder_split_paste_end_keeps_keys(self):
        """端到端：拼接后的记号串经 metrics 统计，粘贴后的退格仍在。"""
        r = Recorder()
        r.recording = True
        r.feed("out", b"\x1b]7770;coach;ON\x07")
        r.feed("in", b"cat > f\r")
        r.feed("out", b"\x1b]7770;coach;S\x07")
        r.feed("in", PASTE_START + b"line1\n")
        r.feed("in", PASTE_END[:-1])
        r.feed("in", PASTE_END[-1:] + b"\x7f\x7f\x7f\x7f")
        r.feed("out", b"\x1b]7770;coach;E;0\x07")
        m = key_metrics(r.render(include_keys=True))
        self.assertEqual(m["program"]["backspace_total"], 4, m)
        self.assertEqual(m["program"]["paste_count"], 1, m)


# ---------- 高2：趋势串线 ----------

class TestTrendNoCrosstalk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fpath = os.path.join(self.tmp.name, "findings.jsonl")
        self.p = mock.patch.object(F, "store_path", lambda: self.fpath)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_program_then_prompt_not_one_series(self):
        F.append_event("proposed", "shell.readline.backspace_run", title="退格")
        F.append_event("accepted", "shell.readline.backspace_run")
        F.append_event("reviewed", "shell.readline.backspace_run", trend="recurring",
                       evidence={"program.max_backspace_run": 34})
        F.append_event("reviewed", "shell.readline.backspace_run", trend="improving",
                       evidence={"prompt.max_backspace_run": 2})
        tips = F.format_tips(F.active_findings())
        self.assertNotIn("34 → 2", tips)
        self.assertIn("已被提出 1 次", tips)

    def test_same_key_still_draws_series_with_label(self):
        F.append_event("proposed", "shell.readline.backspace_run", title="退格")
        F.append_event("accepted", "shell.readline.backspace_run")
        for v in (34, 12, 2):
            F.append_event("reviewed", "shell.readline.backspace_run",
                           trend="improving", evidence={"program.max_backspace_run": v})
        tips = F.format_tips(F.active_findings())
        self.assertIn("program.max_backspace_run: 34 → 12 → 2", tips)

    def test_legacy_flat_key_labeled_separately(self):
        F.append_event("proposed", "shell.readline.backspace_run", title="退格")
        F.append_event("accepted", "shell.readline.backspace_run")
        F.append_event("reviewed", "shell.readline.backspace_run", trend="recurring",
                       evidence={"max_backspace_run": 34})
        F.append_event("reviewed", "shell.readline.backspace_run", trend="improving",
                       evidence={"max_backspace_run": 2})
        F.append_event("reviewed", "shell.readline.backspace_run", trend="improving",
                       evidence={"prompt.max_backspace_run": 1})
        tips = F.format_tips(F.active_findings())
        self.assertIn("旧版汇总", tips)
        self.assertIn("34 → 2", tips)                          # 旧键自成一组
        self.assertNotIn("2 → 1", tips)                        # 不跨到分通道键
        self.assertNotIn("program.max_backspace_run", tips)


# ---------- 中3：旧证据发送前复检 ----------

class TestStaleEvidence(unittest.TestCase):
    def test_old_dirty_evidence_not_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "f.jsonl")
            # 模拟第一版写下的记录：脏键 + NaN 字符串不可用，直接写原始行
            with open(fp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"schema": 1, "event": "proposed",
                                    "finding_id": "a.b.c", "date": "2026-11-18",
                                    "evidence": {"max_backspace_run": 5,
                                                 "file": "/home/user/secret.c"}}) + "\n")
            with mock.patch.object(F, "store_path", lambda: fp):
                req = llm.build_request("## 命令 1  rc=0  t=1.0s\n命令: ls\n",
                                        {"model": "m"})
            user = req["messages"][1]["content"]
            self.assertNotIn("secret", user)
            self.assertIn("max_backspace_run", user)


if __name__ == "__main__":
    unittest.main(verbosity=2)