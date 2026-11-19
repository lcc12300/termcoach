#!/bin/bash
set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
echo "=== 新增诊断测试 + 全量 ==="
python3 -m unittest test_key_diag test_llm test_findings test_improvements test_rec_decoder test_recorder test_counter test_save_policy test_export 2>&1 | grep -E "^(Ran|OK|FAILED)"
echo "=== 重装（让 termcoach 命令用新代码）==="
uv tool install --force --reinstall . 2>&1 | tail -1
echo "=== 验证占位符场景的报错信息（临时造一个坏 config 到隔离目录）==="
python3 - <<'PY'
import os, subprocess, sys, tempfile
with tempfile.TemporaryDirectory() as tmp:
    env = dict(os.environ)
    env["HOME"] = tmp
    for k in ("TERMCOACH_API_KEY", "TERMCOACH_BASE_URL", "TERMCOACH_MODEL"):
        env.pop(k, None)
    cdir = os.path.join(tmp, ".config", "termcoach")
    os.makedirs(cdir)
    with open(os.path.join(cdir, "config.toml"), "w") as f:
        f.write('[llm]\nbase_url = "http://localhost/v1"\nmodel = "m"\napi_key_env = "..."\n')
    tp = os.path.join(tmp, "t.md")
    open(tp, "w").write("# termcoach 录制\n## 命令 1  rc=0  t=1.0s\n命令: ls\n")
    p = subprocess.run([sys.executable, "-m", "termcoach", "review", tp, "--yes"],
                       capture_output=True, text=True, env=env,
                       cwd=os.getcwd())
    print("exit:", p.returncode)
    print(p.stderr)
    print("config show:")
    p2 = subprocess.run([sys.executable, "-m", "termcoach", "config", "show"],
                        capture_output=True, text=True, env=env,
                        cwd=os.getcwd())
    print(p2.stdout)
PY