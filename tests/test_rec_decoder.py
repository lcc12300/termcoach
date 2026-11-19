"""rec_decoder 单测：记号化、跨块、跨回车、粘贴、命令文本提取。"""
import unittest
from termcoach.rec_decoder import InputDecoder, decode_command_text


def dec(chunks):
    d = InputDecoder()
    return "".join(d.feed(c) for c in chunks) + d.flush()


class TestDecode(unittest.TestCase):
    def test_printable_and_cr(self):
        self.assertEqual(dec([b"echo hi\r"]), "echo hi<CR>")

    def test_cross_cr_continuous(self):
        # 跨多个回车不结束（vim 会话）
        self.assertEqual(dec([b"dd", b"5j", b"ciw", b"\x1b", b":w\r"]),
                         "dd5jciw<Esc>:w<CR>")

    def test_arrows(self):
        self.assertEqual(dec([b"\x1b[D\x1b[C\x1b[A\x1b[B"]),
                         "<Left><Right><Up><Down>")

    def test_arrow_cross_chunk(self):
        self.assertEqual(dec([b"\x1b", b"[D"]), "<Left>")

    def test_ctrl_keys(self):
        self.assertEqual(dec([b"\x01\x17\x12\x04"]), "<C-a><C-w><C-r><C-d>")

    def test_backspace(self):
        self.assertEqual(dec([b"\x7f\x08"]), "<BS><BS>")

    def test_alt(self):
        self.assertEqual(dec([b"\x1bb\x1bf"]), "<M-b><M-f>")

    def test_paste(self):
        self.assertEqual(dec([b"\x1b[200~hello\rworld\x1b[201~"]),
                         "<paste>hello\rworld</paste>")

    def test_paste_cross_chunk(self):
        self.assertEqual(dec([b"\x1b[200~", b"abc", b"\x1b[201~"]),
                         "<paste>abc</paste>")

    def test_unknown_csi_not_guess(self):
        r = dec([b"\x1b[99~"])
        self.assertIn("<Esc?>", r)

    def test_vim_like_sequence(self):
        # 典型 vim 编辑：普通模式移动 + 插入 + 退出
        self.assertEqual(dec([b"jjji", b"fix", b"\x1b", b":wq\r"]),
                         "jjji fix<Esc>:wq<CR>".replace(" ", ""))


class TestCommandText(unittest.TestCase):
    def test_strip_cr(self):
        self.assertEqual(decode_command_text("make modules<CR>"), "make modules")

    def test_strip_nl(self):
        self.assertEqual(decode_command_text("ls<NL>"), "ls")

    def test_keeps_edit_marks(self):
        # 含编辑键不重建，保留记号
        self.assertEqual(decode_command_text("ma<BS>ake<CR>"), "ma<BS>ake")


if __name__ == "__main__":
    unittest.main(verbosity=2)