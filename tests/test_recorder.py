"""recorder 单测：三段分离、默认暂停、record on/off、空行、分段限量、双版本渲染。"""
import unittest
from termcoach.recorder import Recorder, trim_output

S = b"\x1b]7770;coach;S\x07"
def E(rc): return f"\x1b]7770;coach;E;{rc}\x07".encode()
ON = b"\x1b]7770;coach;ON\x07"
OFF = b"\x1b]7770;coach;OFF\x07"


class TestRecording(unittest.TestCase):
    def test_default_paused(self):
        r = Recorder()
        r.feed("out", S)
        r.feed("in", b"secret\r")
        r.feed("out", b"output" + E(0))
        self.assertEqual(r.commands, [])  # 默认暂停，零录制

    def test_on_off(self):
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"echo hi\r")     # 提示符编辑
        r.feed("out", S)
        r.feed("out", b"hi\n" + E(0))
        r.feed("out", OFF)
        r.feed("in", b"secret\r")      # 暂停后不录
        r.feed("out", S + b"x" + E(0))
        self.assertEqual(len(r.commands), 1)
        self.assertEqual(r.commands[0].text, "echo hi")
        self.assertEqual(r.commands[0].rc, 0)

    def test_three_way_split(self):
        # P5 模型：命令文本 vs 程序输入分离
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"cat\r")         # 命令文本
        r.feed("out", S)
        r.feed("in", b"line-one\r")    # 程序输入
        r.feed("out", b"line-one\n")   # 程序输出
        r.feed("in", b"\x04")
        r.feed("out", E(0))
        c = r.commands[0]
        self.assertEqual(c.text, "cat")
        self.assertIn("line-one", c.prog_input)
        self.assertNotIn("line-one", c.text)
        self.assertIn("line-one", c.output)

    def test_empty_line_no_block(self):
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"\r")            # 空行
        r.feed("out", E(0))            # 有 E 无 S
        self.assertEqual(r.commands, [])

    def test_unknown_text(self):
        r = Recorder()
        r.feed("out", ON)
        r.feed("out", S)               # 无提示符输入
        r.feed("out", b"x" + E(0))
        self.assertIsNone(r.commands[0].text)

    def test_vim_keys_cross_cr(self):
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"vim a.c\r")
        r.feed("out", S)
        r.feed("in", b"dd")
        r.feed("in", b"5j")
        r.feed("in", b"ciwfix\x1b")
        r.feed("in", b":wq\r")
        r.feed("out", E(0))
        c = r.commands[0]
        self.assertEqual(c.text, "vim a.c")
        self.assertEqual(c.prog_input, "dd5jciwfix<Esc>:wq<CR>")

    def test_two_versions(self):
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"vim a.c\r")
        r.feed("out", S)
        r.feed("in", b"dd:wq\r")
        r.feed("out", E(0))
        base = r.render(include_keys=False)
        keys = r.render(include_keys=True)
        self.assertNotIn("程序输入", base)
        self.assertIn("程序输入", keys)
        self.assertIn("dd", keys)


class TestTrim(unittest.TestCase):
    def test_short_unchanged(self):
        self.assertEqual(trim_output("a\nb"), "a\nb")

    def test_long_keeps_head_err_tail(self):
        lines = [f"line {i} " + "x" * 40 for i in range(200)]
        lines[100] = "hello.c:12: error: expected ';'"
        text = "\n".join(lines)
        self.assertGreater(len(text.encode()), 4096)  # 确保超阈值
        out = trim_output(text)
        self.assertIn("line 0", out)          # 开头
        self.assertIn("error: expected", out)  # 错误附近
        self.assertIn("line 199", out)         # 末尾
        self.assertIn("省略", out)
        self.assertLess(len(out), len(text))


if __name__ == "__main__":
    unittest.main(verbosity=2)