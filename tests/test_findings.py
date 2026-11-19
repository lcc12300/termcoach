"""M1 记忆层测试：双维度状态、事件合并、校验拒写、事实统计、趋势展示。
对应评审最小验收 1/2/6/7/8。
"""
import glob
import json
import os
import tempfile
import unittest

from termcoach import findings as F
from termcoach.metrics import key_metrics


class TestFindingsLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "findings.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_three_reviews(self):
        # 验收1：同一 finding_id 跨三次复盘正确合并
        fid = "shell.readline.backspace_run"
        F.append_event("proposed", fid, path=self.path, title="连续退格过多",
                       evidence={"max_backspace_run": 35}, confidence=0.9)
        F.append_event("accepted", fid, path=self.path)
        F.append_event("reviewed", fid, path=self.path, trend="recurring",
                       evidence={"max_backspace_run": 12})
        F.append_event("reviewed", fid, path=self.path, trend="improving",
                       evidence={"max_backspace_run": 2})
        data = F.load_findings(self.path)
        f = data[fid]
        self.assertEqual(f["times_seen"], 1)  # proposed 只一次
        self.assertEqual(f["acceptance"], "accepted")
        self.assertEqual(f["trend"], "improving")
        nums = [h["evidence"]["max_backspace_run"] for h in f["history"]]
        self.assertEqual(nums, [35, 12, 2])  # 验收8：真实趋势

    def test_acceptance_trend_independent(self):
        # 验收2：AI 又提一次 ≠ 采纳；本次没出现 ≠ 消退
        fid = "vim.insert.mode"
        F.append_event("proposed", fid, path=self.path, title="t")
        F.append_event("proposed", fid, path=self.path, title="t")
        f = F.load_findings(self.path)[fid]
        self.assertEqual(f["times_seen"], 2)
        self.assertEqual(f["acceptance"], "proposed")  # 未被接受
        self.assertEqual(f["trend"], "unknown")
        F.append_event("dismissed", fid, path=self.path)
        self.assertEqual(F.load_findings(self.path)[fid]["acceptance"], "dismissed")

    def test_active_only_accepted_not_resolved(self):
        F.append_event("proposed", "a.b.c", path=self.path, title="t")
        F.append_event("proposed", "a.b.d", path=self.path, title="t")
        F.append_event("accepted", "a.b.d", path=self.path)
        F.append_event("proposed", "a.b.e", path=self.path, title="t")
        F.append_event("accepted", "a.b.e", path=self.path)
        F.append_event("reviewed", "a.b.e", path=self.path, trend="resolved")
        active = F.active_findings(self.path)
        self.assertEqual([f["finding_id"] for f in active], ["a.b.d"])

    def test_validation_rejects_bad(self):
        # 验收6：非法输入不污染
        with self.assertRaises(ValueError):
            F.append_event("proposed", "BAD ID!", path=self.path, title="t")
        with self.assertRaises(ValueError):
            F.append_event("proposed", "a.b.c", path=self.path, confidence=1.5)
        with self.assertRaises(ValueError):
            F.append_event("proposed", "a.b.c", path=self.path,
                           recommendation="x" * 600)
        with self.assertRaises(ValueError):
            F.append_event("proposed", "a.b.c", path=self.path,
                           evil_field="rm -rf /")
        with self.assertRaises(ValueError):
            F.append_event("reviewed", "a.b.c", path=self.path, trend="bogus")
        self.assertEqual(F.load_findings(self.path), {})

    def test_no_raw_content_fields(self):
        # 验收7：白名单外字段（命令原文/路径）拒写
        with self.assertRaises(ValueError):
            F.append_event("proposed", "a.b.c", path=self.path,
                           command="cat /etc/passwd")

    def test_file_perms(self):
        F.append_event("proposed", "a.b.c", path=self.path, title="t")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_corrupt_line_skipped(self):
        F.append_event("proposed", "a.b.c", path=self.path, title="t")
        with open(self.path, "a") as f:
            f.write("{broken json\n")
        F.append_event("accepted", "a.b.c", path=self.path)
        f = F.load_findings(self.path)["a.b.c"]
        self.assertEqual(f["acceptance"], "accepted")

    def test_tips_show_trend(self):
        fid = "shell.readline.backspace_run"
        F.append_event("proposed", fid, path=self.path, title="连续退格过多",
                       recommendation="删除整词时尝试 Ctrl-W",
                       verification="连续退格不超过 2 次",
                       evidence={"max_backspace_run": 35})
        F.append_event("accepted", fid, path=self.path)
        F.append_event("reviewed", fid, path=self.path, trend="improving",
                       evidence={"max_backspace_run": 2})
        tips = F.format_tips(F.active_findings(self.path))
        self.assertIn("35 → 2", tips)
        self.assertIn("Ctrl-W", tips)


class TestMetrics(unittest.TestCase):
    TRANSCRIPT = """# termcoach 录制

## 命令 1  rc=0  t=1.0s
命令: vim x.c
程序输入: i<BS><BS><BS><BS><BS>hello<Esc>:wq<CR>

## 命令 2  rc=2  t=5.0s
命令: gcc x.c
程序输入: <Left><Left><Left><C-a><paste>O0</paste>
输出:
x.c:3: error: x
"""

    def test_counts(self):
        m = key_metrics(self.TRANSCRIPT)
        self.assertEqual(m["backspace_total"], 5)
        self.assertEqual(m["max_backspace_run"], 5)
        self.assertEqual(m["left_right_total"], 3)
        self.assertEqual(m["max_left_right_run"], 3)
        self.assertEqual(m["ctrl_shortcut_counts"], {"C-a": 1})
        self.assertEqual(m["paste_count"], 1)
        self.assertEqual(m["command_count"], 2)
        self.assertEqual(m["failed_command_count"], 1)

    def test_real_transcript(self):
        # cc 第二次真实录制：35 连退格应被准确计数
        cands = sorted(glob.glob(os.path.expanduser("~/.cache/termcoach/transcript_*.md")),
                       key=os.path.getmtime, reverse=True)
        if not cands:
            self.skipTest("真实 transcript 不在")
        p = cands[0]
        m = key_metrics(open(p, encoding="utf-8").read())
        # 35 连退格在 vim 程序输入里（paste 之后）
        self.assertGreaterEqual(m["max_backspace_run"], 30)
        self.assertGreaterEqual(m["paste_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)