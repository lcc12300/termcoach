"""counter.py 单测：两类动作计数 + 五类不支持输入沉默 + 跨块拼接。"""
import unittest
from termcoach.counter import KeyCounter


def feed_all(chunks):
    c = KeyCounter()
    for ch in chunks:
        c.feed(ch)
    return c.summary()


class TestCounting(unittest.TestCase):
    def test_backspace_run(self):
        s = feed_all([b"\x7f\x7f\x7f", b"ab", b"\x7f", b"\r"])
        self.assertEqual(s["backspace_runs"], [3, 1])
        self.assertEqual(s["total_keys"], 6)  # 3bs + 2char + 1bs
        self.assertFalse(s["unsupported"])

    def test_arrow_run_split_by_char(self):
        s = feed_all([b"\x1b[D\x1b[D\x1b[D", b"X", b"\x1b[C\x1b[C", b"\r"])
        self.assertEqual(s["arrow_runs"], [3, 2])
        self.assertFalse(s["unsupported"])

    def test_arrow_split_across_chunks(self):
        # ESC 与 [D 分属两个读取块，必须正常计数（cc 口径修正）
        s = feed_all([b"\x1b", b"[D\x1b[", b"D", b"\r"])
        self.assertEqual(s["arrow_runs"], [2])
        self.assertFalse(s["unsupported"])

    def test_backspace_0x08(self):
        s = feed_all([b"\x08\x08", b"\r"])
        self.assertEqual(s["backspace_runs"], [2])

    def test_shortcuts_counted_not_silenced(self):
        s = feed_all([b"\x01", b"time ", b"\x17", b"\x1bb", b"\r"])
        self.assertEqual(s["ctrl_shortcuts"], {"C-a": 1, "C-w": 1, "M-b": 1})
        self.assertFalse(s["unsupported"])

    def test_enter_not_counted(self):
        s = feed_all([b"abc\r"])
        self.assertEqual(s["total_keys"], 3)


class TestSilence(unittest.TestCase):
    def test_paste_ignored(self):
        s = feed_all([b"\x1b[200~", b"\x7f\x7f\x1b[Dhello\rworld", b"\x1b[201~", b"\x7f", b"\r"])
        self.assertEqual(s["backspace_runs"], [1])  # 只有粘贴外那一次
        self.assertFalse(s["unsupported"])

    def test_paste_with_newline_not_terminate(self):
        s = feed_all([b"\x1b[200~a\nb\x1b[201~", b"\x7f\r"])
        self.assertEqual(s["backspace_runs"], [1])

    def test_ctrl_r_silences(self):
        s = feed_all([b"\x7f\x7f", b"\x12", b"more\x7f\r"])
        self.assertTrue(s["unsupported"])
        self.assertEqual(s["unsupported_reason"], "ctrl-r")

    def test_up_down_history_silences(self):
        for seq in (b"\x1b[A", b"\x1b[B", b"\x1bOA", b"\x1bOB"):
            s = feed_all([seq, b"\x7f\r"])
            self.assertTrue(s["unsupported"], seq)
            self.assertEqual(s["unsupported_reason"], "history-browse")

    def test_unknown_csi_silences(self):
        s = feed_all([b"\x1b[99~", b"\x7f\r"])
        self.assertTrue(s["unsupported"])
        self.assertEqual(s["unsupported_reason"], "unknown-sequence")

    def test_ctrl_c_silences(self):
        s = feed_all([b"abc\x03"])
        self.assertTrue(s["unsupported"])

    def test_unsupported_stops_counting(self):
        s = feed_all([b"\x12", b"\x7f\x7f\x7f\r"])
        self.assertEqual(s["backspace_runs"], [])


class TestEdge(unittest.TestCase):
    def test_lone_esc_then_printable_not_crash(self):
        # ESC 后跟可打印字符（非 b/f/d）→ 跳过，不沉默
        s = feed_all([b"\x1bz", b"\x7f\r"])
        self.assertFalse(s["unsupported"])
        self.assertEqual(s["backspace_runs"], [1])

    def test_empty(self):
        s = feed_all([b"\r"])
        self.assertEqual(s["total_keys"], 0)
        self.assertFalse(s["unsupported"])


if __name__ == "__main__":
    unittest.main(verbosity=2)