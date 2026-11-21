# termcoach · 终端操作教练

[English](README.md) · **中文**

> 录下你选中的那几段操作，让模型指出「哪里本可以更省」，把建议记住，下次提醒你到底改没改。

命令历史告诉你敲了什么，看不出你是怎么敲出来的。

同一条 `gcc -Wall -Wextra main.c` 跑两遍，历史里长得一模一样：一次是 Tab 补全后顺手回车，另一次是补全错了、连退 34 格、重打一遍。只有后者值得复盘，而 `history` 看不见这个差别。

termcoach 记按键记号，把这类差别变成可复核的数字。同一份真实录制，分通道之后是这样：

```text
prompt  : 退格 14 次，最长 3 连     ← 提示符处的命令编辑
program : 退格 35 次，最长 34 连    ← vim 里的输入，34 连退在这儿
```

合并统计只会报「49 次退格 / 最长 34 连」，看不出该先修哪个；分开之后结论很直接：**这次该练 vim，Bash 提示符可以先放一放。**

---

## 特色

### 1. 只录你选中的那一段

默认暂停。提示符处敲 `record on` 开始，`record off` 停；暂停时不收输入、不存输出。SSH、`sudo`、密码操作留在录制窗之外——工具不承诺自动识别密码，只承诺不在你没开录的时候收集。

### 2. 命令归给谁，不靠 `history`

PS0 发开始标记、PROMPT_COMMAND 发结束标记（私有 OSC 7770），三段分离：

| 区间 | 是什么 |
|---|---|
| 上一条结束 → 本条开始 | 命令文本（你在提示符里敲的） |
| 开始 → 结束 | 前台程序（vim、gcc…）的输入与输出 |

拿不到命令文本就标「未知」，不拿按键去猜。不用 `history 1` 是因为实测它会错配：`HISTCONTROL=ignorespace` 下带前导空格的命令取到上一条，空行会沿用上一轮时间，`exit` 甚至没有最后一条记录。

### 3. 提示符和 vim 分开算，粘贴不算按键

按「命令块 × 通道」统计，连击不跨命令、不跨通道。`<paste>…</paste>` 里的字面量 `<BS>` 只是文本，不计按键，只记粘贴次数和正文长度；正文里恰有一行以 `输出:` 开头，也不会把统计提前截断。

### 4. 建议会记住，也会比

`~/.cache/termcoach/findings.jsonl`（600，追加式事件日志，逐条合并），两个维度严格分开：

```text
acceptance:  proposed / accepted / dismissed     你采纳了没有
trend:       unknown / recurring / improving / resolved   行为改没改
```

「AI 又提一次」不等于「你采纳了」；「这次没出现」也不等于「已经改掉」。

下次 `termcoach rec` 启动时按**完全相同的统计键**画趋势：

```text
1. 连续长退格，撤回整段输入
   已被提出 2 次；趋势: improving
   program.max_backspace_run: 34 → 12 → 2
```

vim 的数字不会和提示符的数字被拼成一条「改善」曲线。

### 5. 证据由程序算，模型只能认键名

模型返回的证据必须落在已知统计键上，**数值由本地 metrics 填入**——模型写 `999`、实测 34，存的是 34。路径、命令原文、`NaN`、布尔值、自造统计名一律丢弃，写盘前把「将写入证据」原样打印给你看。

### 6. AI 是可选的一步，不是默认联网

只做一种形态：OpenAI-compatible 的 `/chat/completions`，标准库 `urllib`，零 SDK 依赖。配置只存 `base_url` / `model` / `api_key_env`，密钥走环境变量或不回显的 `config set-key`，**不进命令行参数、不进配置文件、不进仓库**。发送前打印 endpoint、模型、整条请求字节数、密钥来源，你按 `y` 才发。

### 7. 复盘完可以立刻练

模型可附一条练习（任务 / 步骤 / 完成标准 / 预计分钟）。答应就开一个一次性目录和**已经开着录制**的 shell，练完 `exit` 导出，再问一句「现在复盘这次练习？」。不想练回车跳过，不影响记忆——练习是可选的加分项，不是流程关卡。

练习只是文字，程序不执行任何命令。出现明显破坏性的命令（`sudo`、`ssh`、`rm -rf`、`curl`、`mkfs /dev/…`、`dd if=/dev/…` 等）会整条拦下并说明；筛的是**危险参数**而不是单词本身，所以 Vim 的 `dd`（删行）不会被误伤。即便如此也别把它当安全边界——命令是你在自己的 shell 里敲的。

### 8. 配色只在真终端

标题青、编号黄、要敲的命令品红、警告红、说明暗。管道、重定向到文件、`TERM=dumb`、`NO_COLOR` 自动关；颜色不进 transcript，也不进请求体。

---

## 安装

依赖：Linux / WSL（需要 `pty`、`termios`），Python 3.9+（读 `config.toml` 需要 3.11+ 的 `tomllib`）。

```bash
git clone https://github.com/lcc12300/termcoach
cd termcoach
uv tool install .        # 或 pipx install .
```

不想污染全局环境，也可以直接跑：

```bash
python3 -m termcoach rec
```

---

## 快速开始

```bash
termcoach rec                    # 进入可录制会话（默认暂停）
```

1. 提示符处敲 `record on`，开始录
2. 正常干活。录 vim 就在进编辑器前 `on`，退出后 `off`
3. `exit` 结束 → 私有草稿在 `~/.cache/termcoach/`，可先删段、编辑
4. 输 `y` 才导出成正式 `transcript_<时间戳>.md`
5. `termcoach review` 复盘 —— 不带参数就取最新那一份

第一次用 `review` 先配置模型：

```bash
termcoach config init            # 生成 ~/.config/termcoach/config.toml
termcoach config set-key         # 隐藏输入，写 600 权限的本地密钥文件
termcoach config show            # 只看密钥来源，不看值
```

```toml
[llm]
base_url = "https://api.example.com/v1"   # 任意 OpenAI-compatible 端点，本地 vLLM 也行
model = "model-name"
api_key_env = "TERMCOACH_API_KEY"         # 只存变量名
timeout_seconds = 60
```

---

## 命令

| 命令 | 作用 |
|---|---|
| `termcoach rec` | 进入可录制会话（默认暂停，`record on/off` 选段） |
| `termcoach review [路径]` | AI 复盘；不带参数取最新一份，`--pick` 列最近 10 份，`--dry-run` 只看请求 |
| `termcoach tips` | 已确认的待改善事项（纯本地，不联网） |
| `termcoach findings list` | 所有条目的 acceptance / trend / 提出次数 |
| `termcoach findings accept\|dismiss <id>` | 确认追踪 / 忽略 |
| `termcoach metrics [路径]` | 只算事实：分通道退格、连击、方向键、粘贴 |
| `termcoach import-review <json>` | 手工导入分析结果（不调 API） |
| `termcoach config init\|show\|set-key` | 模型配置；密钥不落配置、不落参数 |
| `termcoach drill` | 独立的快捷键小练习（不录真实会话，与录制器互不共用解析） |

---

## 录什么，不录什么

| 会记录 | 不记录 |
|---|---|
| 你选段内的按键记号（`<BS>` `<Left>` `<C-w>` `<Esc>:wq<CR>`） | 暂停期间的一切输入输出 |
| 命令文本、退出码、开始时间 | 原始字节日志 |
| 分段限量的输出（开头 + 第一处错误附近 + 末尾，单条 ≤4 KB） | 屏幕重建帧（首版不做） |
| 分通道事实统计 | 命令原文/路径进入记忆（`findings` 只存统计名与数值） |

硬上限：采集期单条命令 64 KB 丢中段并标注，整条请求序列化后 ≤160 KB。

---

## 已知限制（诚实清单）

- **只支持 POSIX**：依赖 `pty` / `termios`，Windows 原生 Python 跑不了（WSL 可以）。
- **不重建屏幕**：vim 这类全屏程序只提取明文错误行（`E21:` 等），不当屏幕文本；要真帧得引入终端状态解析器，首版没做。
- **不判断 vim 语义**：只产按键记号，同一个 `l` 在普通/插入模式含义不同，语义交给模型并标注推测。
- **粘贴边界**：正文里的 `输出:` / `## 命令` 已处理，跨块结束标记已处理；嵌套 `<paste` 字面量这种极端情况仍可能算错一格。
- **建议上限 3 条**：模型多给会截断并提示，不会因为多一条就把整次复盘作废。

---

## 开发

```bash
python3 -m unittest discover -s tests -t .     # 158 个单测
PYTHONPATH=. python3 tests/test_rec_integration.py   # PTY 集成
bash probes/probe_all.sh                       # 全量 + 重装 + 真实统计
```

仓库带一个 `pre-push` 钩子，推送前会拦下本机路径、服务端口、凭据字面量和误入的录制文件
（启用：`git config core.hooksPath .githooks`）。

```text
termcoach/          包：cli / rec / recorder / rec_decoder / metrics / findings / llm / practice / style / coach
tests/              单测与 PTY 集成（unittest）
probes/             可复跑的验证脚本（hook 归属、OSC 边界、三段分离、配色开关…）
```

---

## License

MIT
