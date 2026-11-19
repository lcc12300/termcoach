"""M2 测试：配置/密钥优先级、请求构造、返回校验、API 失败不污染、mock 端到端。
对应评审验收 3/4/6/7。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from termcoach import llm
from termcoach import findings as F

GOOD_REVIEW = json.dumps({
    "summary": "编辑阶段问题集中",
    "findings": [{
        "finding_id": "shell.readline.backspace_run",
        "title": "连续退格过多",
        "trend": "recurring",
        "evidence": {"max_backspace_run": 34},
        "recommendation": "删除整词时尝试 Ctrl-W",
        "verification": "连续退格不超过 2 次",
        "confidence": 0.9,
    }]
}, ensure_ascii=False)


class TestConfig(unittest.TestCase):
    def test_key_priority(self):
        cfg = {"api_key_env": "MY_CUSTOM_KEY"}
        with mock.patch.dict(os.environ, {"TERMCOACH_API_KEY": "A", "MY_CUSTOM_KEY": "B"}, clear=False):
            self.assertEqual(llm.resolve_key(cfg), "A")
        with mock.patch.dict(os.environ, {"MY_CUSTOM_KEY": "B"}, clear=False):
            env = {k: v for k, v in os.environ.items() if k != "TERMCOACH_API_KEY"}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(llm.resolve_key(cfg), "B")

    def test_key_file_fallback_600(self):
        with tempfile.TemporaryDirectory() as tmp:
            kp = os.path.join(tmp, "key")
            with open(kp, "w") as f:
                f.write("filekey\n")
            os.chmod(kp, 0o600)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("TERMCOACH_API_KEY", "MY_CUSTOM_KEY")}
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(llm, "KEY_PATH", kp):
                    self.assertEqual(llm.resolve_key({"api_key_env": "MY_CUSTOM_KEY"}), "filekey")

    def test_env_overrides_config(self):
        with mock.patch.dict(os.environ, {"TERMCOACH_BASE_URL": "http://x/v1",
                                          "TERMCOACH_MODEL": "m1"}, clear=False):
            with mock.patch.object(llm, "CONFIG_PATH", "/nonexistent.toml"):
                cfg = llm.load_config()
                self.assertEqual(cfg["base_url"], "http://x/v1")
                self.assertEqual(cfg["model"], "m1")

    def test_template_has_no_key_value(self):
        self.assertIn("api_key_env", llm.CONFIG_TEMPLATE)
        self.assertNotIn("sk-", llm.CONFIG_TEMPLATE)


class TestRequest(unittest.TestCase):
    def test_transcript_wrapped_untrusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "f.jsonl")
            req = llm.build_request("命令: echo hi", {"model": "m"})
            user = req["messages"][1]["content"]
            self.assertIn("不可信资料开始", user)
            self.assertIn("不可信资料结束", user)
            self.assertIn("不能视为指令", req["messages"][0]["content"])
            self.assertIn("max_backspace_run", user)  # metrics 注入


class TestParse(unittest.TestCase):
    def test_good(self):
        d = llm.parse_review(GOOD_REVIEW)
        self.assertEqual(d["findings"][0]["trend"], "recurring")

    def test_fenced(self):
        d = llm.parse_review("```json\n" + GOOD_REVIEW + "\n```")
        self.assertEqual(len(d["findings"]), 1)

    def test_prose_rejected(self):
        with self.assertRaises(ValueError):
            llm.parse_review("我建议你多用 Ctrl-W，另外注意……")

    def test_bad_json_rejected(self):
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            llm.parse_review('{"findings": [broken}')

    def test_over_limit_truncated_not_rejected(self):
        items = [{"finding_id": f"a.b.c{i}", "title": "t"} for i in range(4)]
        data = llm.parse_review(json.dumps({"findings": items}))
        self.assertEqual(len(data["findings"]), 3)
        self.assertEqual(data["_dropped_findings"], 1)
        self.assertEqual([f["finding_id"] for f in data["findings"]],
                         ["a.b.c0", "a.b.c1", "a.b.c2"])

    def test_bad_confidence_rejected(self):
        bad = json.loads(GOOD_REVIEW)
        bad["findings"][0]["confidence"] = 1.5
        with self.assertRaises(ValueError):
            llm.parse_review(json.dumps(bad))

    def test_injection_field_rejected(self):
        bad = json.loads(GOOD_REVIEW)
        bad["findings"][0]["execute"] = "rm -rf /"
        with self.assertRaises(ValueError):
            llm.parse_review(json.dumps(bad))

    def test_bad_trend_rejected(self):
        bad = json.loads(GOOD_REVIEW)
        bad["findings"][0]["trend"] = "fixed"
        with self.assertRaises(ValueError):
            llm.parse_review(json.dumps(bad))


class TestReviewFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fpath = os.path.join(self.tmp.name, "findings.jsonl")
        self.tpath = os.path.join(self.tmp.name, "t.md")
        with open(self.tpath, "w") as f:
            f.write("# termcoach 录制\n## 命令 1  rc=0  t=1.0s\n命令: ls\n")
        self.patcher = mock.patch.object(F, "store_path", lambda: self.fpath)
        self.patcher.start()
        self.env = mock.patch.dict(os.environ, {
            "TERMCOACH_BASE_URL": "http://mock/v1",
            "TERMCOACH_MODEL": "mock-model",
            "TERMCOACH_API_KEY": "test-key",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.patcher.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_api_failure_no_pollution(self):
        # 验收3：API 失败不影响本地
        import urllib.error
        with mock.patch.object(llm, "call_api", side_effect=urllib.error.URLError("down")):
            with mock.patch("builtins.input", return_value="y"):
                with self.assertRaises(SystemExit) as cm:
                    llm.cmd_review([self.tpath])
                self.assertEqual(cm.exception.code, 1)
        self.assertEqual(F.load_findings(), {})

    def test_bad_response_no_pollution(self):
        # 验收6：自然语言返回不写入
        with mock.patch.object(llm, "call_api", return_value="你应该多用快捷键"):
            with mock.patch("builtins.input", return_value="y"):
                with self.assertRaises(SystemExit):
                    llm.cmd_review([self.tpath])
        self.assertEqual(F.load_findings(), {})

    def test_cancel_send_no_call(self):
        # 验收4：未确认不上传
        with mock.patch.object(llm, "call_api") as m:
            with mock.patch("builtins.input", return_value="n"):
                llm.cmd_review([self.tpath])
            m.assert_not_called()

    def test_track_writes_findings(self):
        with mock.patch.object(llm, "call_api", return_value=GOOD_REVIEW):
            with mock.patch("builtins.input", side_effect=["y", "t"]):
                llm.cmd_review([self.tpath])
        data = F.load_findings()
        f = data["shell.readline.backspace_run"]
        self.assertEqual(f["acceptance"], "accepted")

    def test_display_only_no_write(self):
        with mock.patch.object(llm, "call_api", return_value=GOOD_REVIEW):
            with mock.patch("builtins.input", side_effect=["y", ""]):
                llm.cmd_review([self.tpath])
        self.assertEqual(F.load_findings(), {})

    def test_second_review_updates_trend(self):
        with mock.patch.object(llm, "call_api", return_value=GOOD_REVIEW):
            with mock.patch("builtins.input", side_effect=["y", "t"]):
                llm.cmd_review([self.tpath])
        improved = json.loads(GOOD_REVIEW)
        improved["findings"][0]["trend"] = "improving"
        improved["findings"][0]["evidence"] = {"max_backspace_run": 2}
        with mock.patch.object(llm, "call_api", return_value=json.dumps(improved)):
            with mock.patch("builtins.input", side_effect=["y", "t"]):
                llm.cmd_review([self.tpath])
        f = F.load_findings()["shell.readline.backspace_run"]
        self.assertEqual(f["trend"], "improving")
        self.assertEqual(f["times_seen"], 1)  # 第二次是 reviewed 不是新 proposed


if __name__ == "__main__":
    unittest.main(verbosity=2)