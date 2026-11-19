"""首次真实录制暴露问题的改进测试：
1. SGR 鼠标上报 / 终端查询响应 / OSC 应答 静默过滤（含跨块）
2. 全屏输出只提取明文错误行，不堆转义碎片
3. record on/off 自身不成块
"""
import unittest
from termcoach.rec_decoder import InputDecoder
from termcoach.recorder import Recorder, extract_screen_text

ON = b"\x1b]7770;coach;ON\x07"
S = b"\x1b]7770;coach;S\x07"
def E(rc): return f"\x1b]7770;coach;E;{rc}\x07".encode()


def dec(chunks):
    d = InputDecoder()
    return "".join(d.feed(c) for c in chunks) + d.flush()


class TestNoiseFilter(unittest.TestCase):
    def test_sgr_mouse_dropped(self):
        # cc transcript 里的真实噪声
        noise = b"\x1b[<64;22;6M\x1b[<64;22;6M\x1b[<0;8;8m"
        self.assertEqual(dec([b"dd", noise, b"jj"]), "ddjj")

    def test_mouse_cross_chunk(self):
        self.assertEqual(dec([b"\x1b[<64;", b"22;6M", b"x"]), "x")

    def test_cursor_report_dropped(self):
        # cc transcript: <Esc?>2;2R <Esc?>3;1R
        self.assertEqual(dec([b"\x1b[2;2R\x1b[3;1R", b"i"]), "i")

    def test_device_attr_dropped(self):
        self.assertEqual(dec([b"\x1b[>0;10;1c", b"a"]), "a")

    def test_decrqm_dropped(self):
        self.assertEqual(dec([b"\x1b[?12;2$y", b"a"]), "a")

    def test_osc_color_response_dropped(self):
        # cc transcript: ESC]10;rgb:cccc/cccc/cccc ESC\
        self.assertEqual(dec([b"\x1b]10;rgb:cccc/cccc/cccc\x1b\\", b"b"]), "b")
        self.assertEqual(dec([b"\x1b]11;rgb:0c0c/0c0c/0c0c\x07", b"c"]), "c")

    def test_real_keys_survive_mixed_noise(self):
        seq = (b"\x1b[<64;22;6M" b"i" b"\x1b[2;2R" b"hello"
               b"\x1b[<0;8;8m" b"\x1b" b":wq\r")
        self.assertEqual(dec([seq]), "ihello<Esc>:wq<CR>")

    def test_arrows_still_work(self):
        self.assertEqual(dec([b"\x1b[D\x1b[C"]), "<Left><Right>")

    def test_focus_events_dropped(self):
        # cc 第二次录制里的 <Esc?>O <Esc?>I：vim 1004h 焦点上报
        self.assertEqual(dec([b"i\x1b[O\x1b[I", b"hello"]), "ihello")


class TestFullscreenOutput(unittest.TestCase):
    def test_extract_vim_errors(self):
        raw = ("\x1b[?1049h\x1b[30;1H\x1b[1m\x1b[41m"
               "E21: Cannot make changes, 'modifiable' is off"
               "\x1b[9;2H\x1b[m more \x1b[30;1H"
               "E45: 'readonly' option is set (add ! to override)"
               "\x1b[?1049l")
        out = extract_screen_text(raw)
        self.assertIn("E21: Cannot make changes", out)
        self.assertIn("E45: 'readonly' option is set", out)
        self.assertIn("非屏幕重建", out)
        self.assertNotIn("\x1b", out)

    def test_extract_directory_notice(self):
        raw = '\x1b[30;1H"demo.c/" is a directory\x1b[2;1H Netrw Directory Listing'
        out = extract_screen_text(raw)
        self.assertIn("is a directory", out)

    def test_no_errors_marks_omitted(self):
        out = extract_screen_text("\x1b[?1049h\x1b[H\x1b[2J redraw stuff\x1b[?1049l")
        self.assertIn("未提取到", out)
        self.assertNotIn("redraw", out)

    def test_recorder_fullscreen_pipeline(self):
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"vim x.c\r")
        r.feed("out", S)
        r.feed("out", b"\x1b[?1049h\x1b[30;1HE45: 'readonly' option is set\x1b[?1049l" + E(0))
        c = r.commands[0]
        self.assertTrue(c.fullscreen)
        self.assertIn("E45", c.output)
        self.assertNotIn("\x1b", c.output)

    def test_normal_output_unaffected(self):
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"make\r")
        r.feed("out", S + b"cc hello.c\nhello.c:3: error: x\n" + E(2))
        c = r.commands[0]
        self.assertFalse(c.fullscreen)
        self.assertIn("error: x", c.output)

    def test_gcc_color_stripped(self):
        # 真实 gcc 彩色诊断（cc 第二次录制）：SGR+擦行尾应删净，明文保留
        raw = ("\x1b[01m\x1b[Kdemo.c:\x1b[m\x1b[K In function "
               "\x1b[01;35m\x1b[Kwarning: \x1b[m\x1b[Kunused parameter\n")
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"gcc x.c\r")
        r.feed("out", S + raw.encode() + E(0))
        c = r.commands[0]
        self.assertNotIn("\x1b", c.output)
        self.assertIn("warning: unused parameter", c.output)


class TestRecordSelfBlock(unittest.TestCase):
    def test_record_on_off_not_rendered(self):
        r = Recorder()
        r.feed("out", ON)
        r.feed("in", b"record on\r")
        r.feed("out", S + E(0))
        r.feed("in", b"echo hi\r")
        r.feed("out", S + b"hi\n" + E(0))
        r.feed("in", b"record off\r")
        r.feed("out", S + E(0))
        out = r.render(include_keys=True)
        self.assertNotIn("record on", out)
        self.assertNotIn("record off", out)
        self.assertIn("echo hi", out)
        self.assertIn("## 命令 1 ", out)  # 编号连续


if __name__ == "__main__":
    unittest.main(verbosity=2)