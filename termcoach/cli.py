"""termcoach 命令行入口：rec | drill | tips | findings | metrics | import-review"""
import argparse
import json
import sys


def _cmd_tips():
    from . import style
    from .findings import active_findings, format_tips
    tips = format_tips(active_findings(), style.enabled())
    print(tips if tips else "[termcoach] 暂无已确认的待改善事项。")


def _cmd_findings(argv):
    from . import findings as F
    from . import style
    on = style.enabled()
    ap = argparse.ArgumentParser(prog="termcoach findings")
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    for act in ("accept", "dismiss"):
        p = sub.add_parser(act)
        p.add_argument("finding_id")
    args = ap.parse_args(argv)
    if args.action == "list":
        data = F.load_findings()
        if not data:
            print("（无 findings）")
            return
        for f in data.values():
            print(f"{style.accent(f['finding_id'], on)}  "
                  f"[{style.acceptance(f['acceptance'], on)}/{style.trend(f['trend'], on)}]  "
                  f"{style.dim('提出', on)}={style.num(str(f['times_seen']), on)}  "
                  f"{f.get('title', '')}")
    else:
        event = {"accept": "accepted", "dismiss": "dismissed"}[args.action]
        F.append_event(event, args.finding_id)
        print(style.ok(f"[termcoach] {args.finding_id} → {event}", on))


def _cmd_metrics(argv):
    from .metrics import key_metrics
    ap = argparse.ArgumentParser(prog="termcoach metrics")
    ap.add_argument("transcript")
    args = ap.parse_args(argv)
    with open(args.transcript, encoding="utf-8") as f:
        text = f.read()
    print(json.dumps(key_metrics(text), ensure_ascii=False, indent=2))


def _cmd_import_review(argv):
    """手工导入一份 AI 分析结果（M1：验证 findings 生命周期，不接 API）。

    输入 JSON: {"findings": [{finding_id,title,recommendation,verification,
                evidence:{...},confidence,...}, ...]}
    逐条确认后才写入（proposed 事件）；AI 提过≠用户采纳。
    """
    from . import findings as F
    ap = argparse.ArgumentParser(prog="termcoach import-review")
    ap.add_argument("review_json")
    ap.add_argument("--accept", action="store_true",
                    help="导入后同时标记 accepted（默认只 proposed，需另行 accept）")
    args = ap.parse_args(argv)
    with open(args.review_json, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("findings", [])
    if not isinstance(items, list):
        print("格式错误：findings 必须是数组", file=sys.stderr)
        sys.exit(1)
    for item in items:
        fid = item.get("finding_id", "?")
        print(f"\n发现: {fid}  {item.get('title', '')}")
        print(f"  建议: {item.get('recommendation', '')}")
        print(f"  验收: {item.get('verification', '')}")
        if "evidence" in item:
            clean, dropped = F.sanitize_evidence(item["evidence"])
            if dropped:
                print(f"  （证据剔除 {dropped} 项：只接受「统计名→数值」）")
            item = {**item, "evidence": clean}
        print(f"  将写入证据: {json.dumps(item.get('evidence') or {}, ensure_ascii=False)}")
        ans = input("  记住并追踪？[y/N] ").strip().lower()
        if ans != "y":
            print("  跳过。")
            continue
        fields = {k: v for k, v in item.items() if k != "finding_id"}
        try:
            F.append_event("proposed", fid, **fields)
            if args.accept:
                F.append_event("accepted", fid)
            print(f"  已记录{'（accepted）' if args.accept else '（proposed，用 findings accept 确认追踪）'}")
        except ValueError as e:
            print(f"  校验失败，拒绝写入: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        prog="termcoach",
        description="终端选段录制 + AI 复盘（默认暂停，record on/off 开关，退出预览确认才落盘）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rec", help="进入可录制会话（真实 bash，选段录制）")
    sub.add_parser("drill", help="编辑快捷键练习（合成任务，只编辑不执行）")
    sub.add_parser("tips", help="显示已确认的待改善事项（纯本地，不联网）")
    sub.add_parser("findings", help="list / accept <id> / dismiss <id>")
    sub.add_parser("metrics", help="transcript 确定性事实统计（JSON）")
    sub.add_parser("import-review", help="手工导入 AI 分析 JSON，逐条确认后记忆")
    sub.add_parser("review", help="调用 OpenAI-compatible API 复盘 transcript（显式确认才发送）")
    sub.add_parser("config", help="init / show / set-key（LLM 配置，密钥不进配置文件）")
    args, extra = ap.parse_known_args()
    if args.cmd == "tips":
        _cmd_tips()
    elif args.cmd == "findings":
        _cmd_findings(extra)
    elif args.cmd == "metrics":
        _cmd_metrics(extra)
    elif args.cmd == "import-review":
        _cmd_import_review(extra)
    elif args.cmd == "review":
        from .llm import cmd_review
        cmd_review(extra)
    elif args.cmd == "config":
        from .llm import cmd_config
        cmd_config(extra)
    else:
        # 子模块自带 argparse（如 drill --save），把剩余参数转交给它
        sys.argv = [f"termcoach {args.cmd}"] + extra
        if args.cmd == "rec":
            from .rec import main as run
        else:
            from .coach import main as run
        run()


if __name__ == "__main__":
    main()
