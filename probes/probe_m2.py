#!/usr/bin/env python3
"""M2 真实 HTTP 冒烟：本地 mock OpenAI-compatible 服务器，走完整 urllib 链路。
验证请求格式、鉴权头、响应解析、findings 落盘（隔离 HOME）。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

REVIEW = {
    "summary": "编辑阶段问题集中",
    "findings": [{
        "finding_id": "shell.readline.backspace_run",
        "title": "连续退格过多",
        "evidence": {"max_backspace_run": 34},
        "recommendation": "删除整词时尝试 Ctrl-W",
        "verification": "连续退格不超过 2 次",
        "confidence": 0.9,
    }]
}

received = {}

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        received["path"] = self.path
        received["auth"] = self.headers.get("Authorization")
        received["model"] = body.get("model")
        received["n_messages"] = len(body.get("messages", []))
        resp = {"choices": [{"message": {"content": json.dumps(REVIEW, ensure_ascii=False)}}]}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass

srv = HTTPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

with tempfile.TemporaryDirectory() as home:
    tpath = os.path.join(home, "t.md")
    with open(tpath, "w") as f:
        f.write("# termcoach 录制\n## 命令 1  rc=0  t=1.0s\n命令: vim x.c\n程序输入: i<BS><BS><BS><Esc>:wq<CR>\n")
    env = dict(os.environ)
    env.update({
        "HOME": home,
        "TERMCOACH_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "TERMCOACH_MODEL": "mock-model",
        "TERMCOACH_API_KEY": "test-secret",
    })
    # 走真实 CLI 入口（uv tool 安装的版本可能旧，用源码模块方式）
    p = subprocess.run(
        [sys.executable, "-m", "termcoach", "review", tpath, "--yes"],
        input="t\n", capture_output=True, text=True, env=env,
        cwd=__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))), timeout=30)
    print("exit:", p.returncode)
    print(p.stdout[-600:])
    if p.stderr:
        print("STDERR:", p.stderr[-300:])
    fpath = os.path.join(home, ".cache", "termcoach", "findings.jsonl")
    ok = (
        p.returncode == 0
        and received.get("path") == "/v1/chat/completions"
        and received.get("auth") == "Bearer test-secret"
        and received.get("model") == "mock-model"
        and os.path.exists(fpath)
    )
    print("server received:", received)
    if os.path.exists(fpath):
        st = os.stat(fpath)
        print("findings perms:", oct(st.st_mode & 0o777))
        ok = ok and (st.st_mode & 0o777) == 0o600
    srv.shutdown()
    print("SMOKE", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)