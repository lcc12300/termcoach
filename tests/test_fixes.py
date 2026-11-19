"""四项评审修正的回归测试。

高1 粘贴正文不计按键；提示符/程序输入分通道、连击不跨命令。
高2 evidence 只存统计名+数值，写入前展示。
中3 t 可恢复被忽略的条目；计数文案是「提出次数」。
中4 输出有字节硬上限，采集期丢中段。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from termcoach import findings as F
from termcoach import llm
from termcoach.metrics import key_metrics
from termcoach.recorder import (MAX_ACCUM_CHARS, MAX_OUTPUT_BYTES, Recorder,
                                trim_output)


# ---------- 高1：粘贴与通道 ----------

class TestPasteNotKeys(unittest.TestCase):
    def test_literal_bs_inside_paste_not_counted(self):
        t = ("## 命令 1  rc=0  t=1.0s\n"
             "命令: cat > f\n"
             "程序输入: <paste>x<BS>y<BS>z</paste><CR>\n")
        m = key_metrics(t)
        self.assertEqual(m["backspace_total"], 0)          # 正文里的 <BS> 不是按键
        self.assertEqual(m["program"]["paste_count"], 1)
        self.assertGreater(m["program"]["paste_chars"], 0)  # 但记下粘贴规模

    def test_real_bs_outside_paste_still_counted(self):
        t = ("## 命令 1  rc=0  t=1.0s\n"
             "命令: ls\n"
             "程序输入: <BS><BS><paste>x<BS></paste><BS>\n")
        m = key_metrics(t)
        self.assertEqual(m["backspace_total"], 3)

    def test_unclosed_paste_not_counted(self):
        t = ("## 命令 1  rc=0  t=1.0s\n命令: cat\n"
             "程序输入: <paste…未闭合><BS><BS><BS>\n")
        self.assertEqual(key_metrics(t)["backspace_total"], 0)


class TestChannelSplit(unittest.TestCase):
    def test_prompt_and_program_separated(self):
        t = ("## 命令 1  rc=0  t=1.0s\n"
             "命令: gcc x<BS><BS>.c\n"          # 提示符编辑 2 次
             "程序输入: i<BS><BS><BS><BS><Esc>:wq<CR>\n")   # 程序输入 4 次
        m = key_metrics(t)
        self.assertEqual(m["prompt"]["backspace_total"], 2)
        self.assertEqual(m["program"]["backspace_total"], 4)
        self.assertEqual(m["backspace_total"], 6)          # 汇总仍可用
        self.assertEqual(m["prompt"]["max_backspace_run"], 2)
        self.assertEqual(m["program"]["max_backspace_run"], 4)
        self.assertEqual(m["max_backspace_run"], 4)

    def test_runs_do_not_cross_commands(self):
        # 两条命令各 3 连退格，连起来会被误算成 6
        t = ("## 命令 1  rc=0  t=1.0s\n命令: a<BS><BS><BS>\n"
             "## 命令 2  rc=0  t=2.0s\n命令: b<BS><BS><BS>\n")
        m = key_metrics(t)
        self.assertEqual(m["backspace_total"], 6)
        self.assertEqual(m["max_backspace_run"], 3)

    def test_runs_do_not_cross_channels(self):
        t = ("## 命令 1  rc=0  t=1.0s\n"
             "命令: vim<BS><BS><CR>\n"
             "程序输入: <BS><BS><BS>\n")
        self.assertEqual(key_metrics(t)["max_backspace_run"], 3)


# ---------- 高2：evidence 白名单 ----------

class TestEvidenceWhitelist(unittest.TestCase):
    def test_path_dropped(self):
        clean, dropped = F.sanitize_evidence(
            {"max_backspace_run": 34, "file": "/home/user/d1.c", "cmd": "vim x.c"})
        self.assertEqual(clean, {"max_backspace_run": 34})
        self.assertEqual(dropped, 2)

    def test_unknown_metric_key_dropped(self):
        # 第二批评审：自造统计名不能进 evidence（旧版允许任意短数字串）
        clean, dropped = F.sanitize_evidence({"trend_span": "35 -> 2",
                                              "my_custom_metric": 7})
        self.assertEqual(clean, {})
        self.assertEqual(dropped, 2)

    def test_nonfinite_and_bool_dropped(self):
        clean, dropped = F.sanitize_evidence({"max_backspace_run": float("nan"),
                                              "backspace_total": True})
        self.assertEqual(clean, {})
        self.assertEqual(dropped, 2)

    def test_values_taken_from_program_metrics(self):
        # AI 报 999，程序实测 34 → 存 34
        metrics = {"program": {"max_backspace_run": 34}}
        clean, dropped = F.sanitize_evidence(
            {"program.max_backspace_run": 999}, metrics)
        self.assertEqual(clean, {"program.max_backspace_run": 34})
        self.assertEqual(dropped, 0)

    def test_dotted_key_kept(self):
        clean, _ = F.sanitize_evidence({"program.max_backspace_run": 34})
        self.assertIn("program.max_backspace_run", clean)

    def test_validate_rejects_dirty_evidence(self):
        with self.assertRaises(ValueError):
            F._validate_finding({"finding_id": "a.b.c",
                                 "evidence": {"note": "/etc/passwd"}})

    def test_append_event_sanitizes_even_if_caller_forgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "f.jsonl")
            F.append_event("proposed", "a.b.c", path=p,
                           evidence={"max_backspace_run": 5, "x": "/home/user/secret"})
            raw = open(p, encoding="utf-8").read()
            self.assertNotIn("secret", raw)
            self.assertIn("max_backspace_run", raw)

    def test_parse_review_drops_dirty_and_reports(self):
        data = llm.parse_review(json.dumps({
            "findings": [{"finding_id": "a.b.c", "title": "t",
                          "evidence": {"max_backspace_run": 9,
                                       "cmd": "vim /home/user/a.c"}}]}))
        self.assertEqual(data["findings"][0]["evidence"], {"max_backspace_run": 9})
        self.assertEqual(data["_dropped_evidence"], 1)


# ---------- 中3：恢复追踪与文案 ----------

class TestRestoreDismissed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fpath = os.path.join(self.tmp.name, "findings.jsonl")
        self.tpath = os.path.join(self.tmp.name, "t.md")
        with open(self.tpath, "w") as f:
            f.write("## 命令 1  rc=0  t=1.0s\n命令: ls\n")
        self.p1 = mock.patch.object(F, "store_path", lambda: self.fpath)
        self.p1.start()
        self.env = mock.patch.dict(os.environ, {
            "TERMCOACH_BASE_URL": "http://mock/v1",
            "TERMCOACH_MODEL": "m", "TERMCOACH_API_KEY": "k"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.p1.stop()
        self.env.stop()
        self.tmp.cleanup()

    REVIEW = json.dumps({"summary": "s", "findings": [{
        "finding_id": "shell.readline.backspace_run", "title": "退格",
        "evidence": {"max_backspace_run": 34}, "recommendation": "Ctrl-W"}]})

    def test_t_after_dismiss_restores_tracking(self):
        F.append_event("proposed", "shell.readline.backspace_run", title="退格")
        F.append_event("dismissed", "shell.readline.backspace_run")
        self.assertEqual(F.load_findings()["shell.readline.backspace_run"]["acceptance"],
                         "dismissed")
        with mock.patch.object(llm, "call_api", return_value=self.REVIEW):
            with mock.patch("builtins.input", side_effect=["y", "t"]):
                llm.cmd_review([self.tpath])
        f = F.load_findings()["shell.readline.backspace_run"]
        self.assertEqual(f["acceptance"], "accepted")
        # 恢复后应出现在启动提醒里
        self.assertEqual(len(F.active_findings()), 1)

    def test_times_seen_is_proposal_count_wording(self):
        F.append_event("proposed", "a.b.c", title="t")
        F.append_event("accepted", "a.b.c")
        F.append_event("reviewed", "a.b.c", trend="recurring")
        f = F.load_findings()["a.b.c"]
        self.assertEqual(f["times_seen"], 1)          # reviewed 不增加提出次数
        tips = F.format_tips(F.active_findings())
        self.assertIn("已被提出 1 次", tips)
        self.assertNotIn("已出现", tips)

    def test_trend_line_accepts_channel_keys(self):
        # AI 现在用分通道键（program.max_backspace_run），趋势行仍要能画
        F.append_event("proposed", "a.b.c", title="退格")
        F.append_event("accepted", "a.b.c")
        F.append_event("reviewed", "a.b.c", trend="recurring",
                       evidence={"program.max_backspace_run": 34})
        F.append_event("reviewed", "a.b.c", trend="improving",
                       evidence={"program.max_backspace_run": 2})
        tips = F.format_tips(F.active_findings())
        self.assertIn("34 → 2", tips)

    def test_confirm_shows_evidence(self):
        with mock.patch.object(llm, "call_api", return_value=self.REVIEW):
            with mock.patch("builtins.input", side_effect=["y", ""]):
                with mock.patch("builtins.print") as mp:
                    llm.cmd_review([self.tpath])
        out = " ".join(str(a) for c in mp.call_args_list for a in c.args)
        self.assertIn("将写入证据", out)
        self.assertIn("max_backspace_run", out)


# ---------- 中4：字节硬上限 ----------

class TestOutputCaps(unittest.TestCase):
    def test_single_long_line_truncated(self):
        big = "x" * 24000
        out = trim_output(big)
        self.assertLessEqual(len(out.encode()), MAX_OUTPUT_BYTES)   # 严格 ≤4096
        self.assertIn("已省略", out)

    def test_many_lines_truncated(self):
        out = trim_output("\n".join(f"line {i}" for i in range(5000)))
        self.assertLessEqual(len(out.encode()), MAX_OUTPUT_BYTES)

    def test_request_total_bounded(self):
        # 第二批评审：整条序列化请求都要受上限约束（含历史/统计/JSON 开销）
        cfg = {"model": "m"}
        big = "\n".join(f"## 命令 {i}  rc=0  t=1.0s\n命令: ls\n输出:\n" + "y" * 2000
                        for i in range(200))
        req = llm.build_request(big, cfg)
        self.assertLessEqual(llm.request_size(req), llm.MAX_REQUEST_BYTES)
        self.assertIn("已截断", req["messages"][1]["content"])

    def test_small_output_untouched(self):
        self.assertEqual(trim_output("a\nb"), "a\nb")

    def test_accumulation_bounded_during_record(self):
        r = Recorder()
        r.recording = True
        r.feed("out", b"\x1b]7770;coach;ON\x07")
        r.feed("in", b"cat big\r")
        r.feed("out", b"\x1b]7770;coach;S\x07")
        for _ in range(200):
            r.feed("out", b"y" * 4096)        # 800KB 输出
        self.assertLessEqual(len(r._cur.output), MAX_ACCUM_CHARS)
        self.assertGreater(r._cur.dropped, 0)
        r.feed("out", b"\x1b]7770;coach;E;0\x07")
        rendered = r.render(include_keys=False)
        self.assertIn("已丢弃中段", rendered)
        self.assertLess(len(rendered.encode()), 20000)


if __name__ == "__main__":
    unittest.main(verbosity=2)