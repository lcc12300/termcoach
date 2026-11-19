"""验证 --save 口径：不带不建目录；带了只存任务ID+计数，无命令/路径/按键原文。"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from termcoach import coach


class TestSavePolicy(unittest.TestCase):
    def test_no_save_no_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = os.path.join(tmp, "home")
            os.makedirs(fake_home)
            with mock.patch.dict(os.environ, {"HOME": fake_home}):
                with mock.patch.object(coach, "run_edit", return_value=("x", {
                    "total_keys": 1, "backspace_runs": [], "arrow_runs": [],
                    "ctrl_shortcuts": {}, "unsupported": False, "unsupported_reason": None})):
                    coach.run_drill(save_path=None)
            self.assertFalse(os.path.exists(os.path.join(fake_home, ".local")))

    def test_save_only_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "drills.jsonl")
            summary = {"total_keys": 5, "backspace_runs": [3], "arrow_runs": [],
                       "ctrl_shortcuts": {"C-a": 1}, "unsupported": False,
                       "unsupported_reason": None}
            with mock.patch.object(coach, "run_edit", return_value=("SECRET /secret/path", summary)):
                coach.run_drill(save_path=path)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertEqual(len(lines), 3)  # T1 T2 T3
            blob = json.dumps(lines)
            # 不得含命令/路径/按键原文
            self.assertNotIn("SECRET", blob)
            self.assertNotIn("/secret/path", blob)
            self.assertNotIn("prefill", blob)
            for r in lines:
                self.assertIn(r["task"], ("T1", "T2", "T3"))
                self.assertIn("total_keys", r["baseline"])
                self.assertNotIn("line", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)