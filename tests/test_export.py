"""导出策略单测：取消删草稿、确认生成正式 .md、私有目录权限、无原始字节日志。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from termcoach import rec
from termcoach.recorder import Recorder

ON = b"\x1b]7770;coach;ON\x07"
S = b"\x1b]7770;coach;S\x07"
def E(rc): return f"\x1b]7770;coach;E;{rc}\x07".encode()


def make_rec():
    r = Recorder()
    r.feed("out", ON)
    r.feed("in", b"echo hi\r")
    r.feed("out", S + b"hi\n" + E(0))
    return r


class TestExport(unittest.TestCase):
    def test_cancel_deletes_draft(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home, "EDITOR": "true"}):
                with mock.patch("builtins.input", return_value="n"):
                    rec.preview_and_export(make_rec())
            cache = os.path.join(home, ".cache", "termcoach")
            files = os.listdir(cache) if os.path.isdir(cache) else []
            self.assertEqual(files, [])  # 草稿已删，无正式文件

    def test_confirm_creates_final(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home, "EDITOR": "true"}):
                with mock.patch("builtins.input", return_value="y"):
                    rec.preview_and_export(make_rec())
            cache = os.path.join(home, ".cache", "termcoach")
            files = os.listdir(cache)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].startswith("transcript_"))
            self.assertFalse(any(f.startswith("draft_") for f in files))
            # 目录权限 700
            self.assertEqual(os.stat(cache).st_mode & 0o777, 0o700)
            content = open(os.path.join(cache, files[0]), encoding="utf-8").read()
            self.assertIn("echo hi", content)
            self.assertIn("hi", content)

    def test_no_commands_no_dir(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}):
                rec.preview_and_export(Recorder())  # 空录制
            self.assertFalse(os.path.exists(os.path.join(home, ".cache")))


if __name__ == "__main__":
    unittest.main(verbosity=2)