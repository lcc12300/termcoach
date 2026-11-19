"""密钥诊断测试：占位符忽略、逐项诊断、错误信息可操作。"""
import os
import tempfile
import unittest
from unittest import mock

from termcoach import llm


class TestKeyDiagnostics(unittest.TestCase):
    def test_placeholder_env_name_ignored(self):
        # api_key_env = "..." 不是变量名，视为未设置
        self.assertFalse(llm._valid_env_name("..."))
        self.assertFalse(llm._valid_env_name(""))
        self.assertFalse(llm._valid_env_name("A B"))
        self.assertTrue(llm._valid_env_name("TERMCOACH_API_KEY"))
        self.assertTrue(llm._valid_env_name("_x1"))

    def test_placeholder_does_not_resolve(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("TERMCOACH_API_KEY", "...")}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(llm, "KEY_PATH", os.path.join(tmp, "nokey")):
                    self.assertEqual(llm.resolve_key({"api_key_env": "..."}), "")

    def test_sources_listed_with_invalid_name_note(self):
        env = {k: v for k, v in os.environ.items() if k != "TERMCOACH_API_KEY"}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(llm, "KEY_PATH", os.path.join(tmp, "nokey")):
                    src = llm.key_sources({"api_key_env": "..."})
                    joined = " ".join(d for d, _ in src)
                    self.assertIn("非法变量名", joined)
                    self.assertFalse(any(ok for _, ok in src))
                    self.assertEqual(llm.key_source({"api_key_env": "..."}), "（未设置）")

    def test_key_file_counts_as_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            kp = os.path.join(tmp, "key")
            with open(kp, "w") as f:
                f.write("k\n")
            with mock.patch.object(llm, "KEY_PATH", kp):
                src = llm.key_sources({"api_key_env": "TERMCOACH_API_KEY"})
                self.assertTrue(any(ok for _, ok in src))
                self.assertIn(kp, llm.key_source({"api_key_env": "TERMCOACH_API_KEY"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)