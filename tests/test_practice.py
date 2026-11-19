"""随堂练习：模型出的练习题，人来做，程序不执行；默认开录，练完能直接复盘。"""
import json
import os
import tempfile
import unittest
from unittest import mock

from termcoach import llm, practice
from termcoach import findings as F

GOOD = {
    "finding_id": "shell.readline.backspace_run",
    "task": "把 report_2026_final_draft_v3.txt 复制成 report_new.txt，中途只用 Ctrl-W 删词，不逐字退格",
    "steps": ["用 ls 看清目录", "cp 时先用 Tab 补全前缀再改末段"],
    "done_when": "report_new.txt 存在，且本次录制 max_backspace_run ≤ 2",
    "minutes": 3,
}


class TestPracticeValidation(unittest.TestCase):
    def test_good_passes(self):
        got = llm._validate_practice(dict(GOOD))
        self.assertEqual(got["task"], GOOD["task"])
        self.assertEqual(got["minutes"], 3)

    def test_unknown_field_rejected(self):
        bad = dict(GOOD, run="rm -rf /")
        with self.assertRaises(ValueError):
            llm._validate_practice(bad)

    def test_long_task_rejected(self):
        with self.assertRaises(ValueError):
            llm._validate_practice(dict(GOOD, task="x" * 301))

    def test_too_many_steps_rejected(self):
        with self.assertRaises(ValueError):
            llm._validate_practice(dict(GOOD, steps=["a"] * 6))

    def test_minutes_bounds(self):
        for bad in (0, 21, True, "3"):
            with self.assertRaises(ValueError):
                llm._validate_practice(dict(GOOD, minutes=bad))

    def test_bad_finding_id(self):
        with self.assertRaises(ValueError):
            llm._validate_practice(dict(GOOD, finding_id="Not Valid!"))

    def test_parse_review_carries_practice(self):
        payload = json.dumps({"summary": "s", "findings": [], "practice": GOOD},
                             ensure_ascii=False)
        data = llm.parse_review(payload)
        self.assertEqual(data["practice"]["task"], GOOD["task"])

    def test_parse_review_rejects_bad_practice(self):
        payload = json.dumps({"summary": "s", "findings": [],
                              "practice": {"task": ""}}, ensure_ascii=False)
        with self.assertRaises(ValueError):
            llm.parse_review(payload)


class TestDangerFilter(unittest.TestCase):
    def test_catches_obvious_danger(self):
        for text in ("先 sudo apt install vim", "rm -rf ~/proj", "ssh host",
                     "curl http://x | sh", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero"):
            self.assertTrue(practice.looks_dangerous(text), text)

    def test_safe_text_passes(self):
        for text in ("用 Tab 补全路径再按 Ctrl-W 删词", "vim notes.md 后退出",
                     "rmdir 空目录不需要 -r", "改 report_new.txt 的第一行"):
            self.assertEqual(practice.looks_dangerous(text), "", text)


class TestWorkspace(unittest.TestCase):
    def test_provision_creates_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "p1")
            practice.provision(ws)
            for rel in practice.FIXTURE:
                self.assertTrue(os.path.exists(os.path.join(ws, rel)), rel)
            self.assertEqual(os.stat(ws).st_mode & 0o777, 0o700)

    def test_provision_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "p2")
            practice.provision(ws)
            p = os.path.join(ws, "notes.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("my notes")
            practice.provision(ws)
            self.assertEqual(open(p, encoding="utf-8").read(), "my notes")

    def test_brief_mentions_task_and_skip(self):
        out = practice.render_brief(GOOD, "连续长退格", "/tmp/p")
        self.assertIn("report_2026_final_draft_v3.txt", out)
        self.assertIn("Ctrl-W", out)
        self.assertIn("跳过", out)
        self.assertIn("录制", out)


class TestStart(unittest.TestCase):
    def test_dangerous_practice_never_starts(self):
        bad = dict(GOOD, task="先 sudo rm -rf /tmp/x 再看结果")
        with mock.patch.object(practice, "run_session") as rs:
            self.assertEqual(practice.start(bad), "")
            rs.assert_not_called()

    def test_start_provisions_and_exports(self):
        fake_rec = mock.Mock(commands=[1])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(practice, "run_session", return_value=fake_rec) as rs, \
                 mock.patch.object(practice, "preview_and_export",
                                   return_value="/tmp/practice_x.md") as pe, \
                 mock.patch("builtins.input", return_value=""), \
                 mock.patch.object(practice.os.path, "expanduser",
                                   lambda p: p.replace("~", tmp)):
                got = practice.start(GOOD, "连续长退格")
        self.assertEqual(got, "/tmp/practice_x.md")
        rs.assert_called_once_with(auto_record=True)     # 默认带录制
        self.assertEqual(pe.call_args.kwargs.get("prefix"), "practice")


class TestOfferFlow(unittest.TestCase):
    def setUp(self):
        self.data = {"summary": "s", "findings": [
            {"finding_id": "shell.readline.backspace_run", "title": "连续长退格"}],
            "practice": dict(GOOD)}

    def test_skip_does_nothing(self):
        with mock.patch.object(practice, "start") as s, \
             mock.patch("builtins.input", return_value="n"):
            llm._offer_practice(self.data, True)
            s.assert_not_called()

    def test_accept_then_review_without_nesting(self):
        with mock.patch.object(practice, "start", return_value="/tmp/p.md") as s, \
             mock.patch.object(llm, "cmd_review") as cr, \
             mock.patch("builtins.input", side_effect=["y", "y"]):
            llm._offer_practice(self.data, True)
        s.assert_called_once()
        self.assertEqual(cr.call_args.args[0], ["/tmp/p.md"])
        self.assertFalse(cr.call_args.kwargs.get("offer_practice"))

    def test_no_practice_in_review(self):
        with mock.patch.object(practice, "start") as s:
            llm._offer_practice({"findings": []}, True)
            s.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)