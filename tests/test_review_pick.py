"""便利入口：review 不带参数取最新一份、--pick 选编号、缺文件报错。

保证"更便利"不牺牲"知道自己在分析哪一份"：选定后一定打印文件名与时间。
"""
import os
import tempfile
import unittest
from unittest import mock

from termcoach import findings as F
from termcoach import llm

REVIEW = '{"summary":"s","findings":[]}'


class TestReviewPicksTranscript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = self.tmp.name
        self.fpath = os.path.join(self.tmp.name, "findings.jsonl")
        self._patches = [
            mock.patch.object(llm, "CACHE_DIR", self.cache),
            mock.patch.object(F, "store_path", lambda: self.fpath),
            mock.patch.dict(os.environ, {
                "TERMCOACH_BASE_URL": "http://mock/v1",
                "TERMCOACH_MODEL": "m", "TERMCOACH_API_KEY": "k"}, clear=False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _make(self, name, body, mtime):
        p = os.path.join(self.cache, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        os.utime(p, (mtime, mtime))
        return p

    def test_recent_sorted_newest_first(self):
        self._make("transcript_20260101_000000.md", "## 命令 1  rc=0  t=1.0s\n", 1000)
        self._make("transcript_20260202_000000.md", "## 命令 2  rc=0  t=1.0s\n", 2000)
        self._make("draft_x.md", "草稿不该出现", 3000)
        paths = llm.recent_transcripts()
        self.assertEqual([os.path.basename(p) for p in paths],
                         ["transcript_20260202_000000.md", "transcript_20260101_000000.md"])

    def test_no_arg_uses_newest_and_names_it(self):
        self._make("transcript_20260101_000000.md", "## 命令 1  rc=0  t=1.0s\n", 1000)
        self._make("transcript_20260202_000000.md", "## 命令 2  rc=0  t=1.0s\n", 2000)
        sent = {}

        def fake_api(request, cfg, key):
            sent["user"] = request["messages"][1]["content"]
            return REVIEW

        with mock.patch.object(llm, "call_api", side_effect=fake_api):
            with mock.patch("builtins.input", return_value="y"):
                with mock.patch("builtins.print") as mp:
                    llm.cmd_review([])
        out = " ".join(str(a) for c in mp.call_args_list for a in c.args)
        self.assertIn("transcript_20260202_000000.md", out)   # 用户看得见选了哪份
        self.assertIn("## 命令 2", sent["user"])               # 发出去的确实是新版

    def test_pick_menu_by_number(self):
        self._make("transcript_20260101_000000.md", "## 命令 1  rc=0  t=1.0s\n", 1000)
        self._make("transcript_20260202_000000.md", "## 命令 2  rc=0  t=1.0s\n", 2000)
        sent = {}

        def fake_api(request, cfg, key):
            sent["user"] = request["messages"][1]["content"]
            return REVIEW

        with mock.patch.object(llm, "call_api", side_effect=fake_api):
            with mock.patch("builtins.input", side_effect=["2", "y"]):
                llm.cmd_review(["--pick"])
        self.assertIn("## 命令 1", sent["user"])               # 选第 2 条=较旧那份

    def test_pick_enter_defaults_to_newest(self):
        self._make("transcript_20260101_000000.md", "## 命令 1  rc=0  t=1.0s\n", 1000)
        self._make("transcript_20260202_000000.md", "## 命令 2  rc=0  t=1.0s\n", 2000)
        sent = {}

        def fake_api(request, cfg, key):
            sent["user"] = request["messages"][1]["content"]
            return REVIEW

        with mock.patch.object(llm, "call_api", side_effect=fake_api):
            with mock.patch("builtins.input", side_effect=["", "y"]):
                llm.cmd_review(["--pick"])
        self.assertIn("## 命令 2", sent["user"])

    def test_no_transcript_at_all_exits(self):
        with self.assertRaises(SystemExit) as cm:
            llm.cmd_review([])
        self.assertEqual(cm.exception.code, 1)

    def test_missing_path_exits_before_sending(self):
        with mock.patch.object(llm, "call_api") as m:
            with self.assertRaises(SystemExit):
                llm.cmd_review([os.path.join(self.tmp.name, "nope.md")])
            m.assert_not_called()

    def test_explicit_path_still_wins(self):
        old = self._make("transcript_20260101_000000.md", "## 命令 1  rc=0  t=1.0s\n", 1000)
        self._make("transcript_20260202_000000.md", "## 命令 2  rc=0  t=1.0s\n", 2000)
        sent = {}

        def fake_api(request, cfg, key):
            sent["user"] = request["messages"][1]["content"]
            return REVIEW

        with mock.patch.object(llm, "call_api", side_effect=fake_api):
            with mock.patch("builtins.input", return_value="y"):
                llm.cmd_review([old])
        self.assertIn("## 命令 1", sent["user"])


if __name__ == "__main__":
    unittest.main(verbosity=2)