"""findings 记忆层：追加式事件日志 + 双维度状态（评审 §一/§二）。

acceptance（用户是否采纳）: proposed / accepted / dismissed
trend（行为是否改善）: unknown / recurring / improving / resolved
两个维度独立，"AI 又提一次"≠采纳，"本次没出现"≠消退。

事件追加到 findings.jsonl（600），读取时按 finding_id 合并当前状态。
只存摘要与统计，不存命令原文/路径/输出全文/密钥。
"""
import json
import os
import re
import time

SCHEMA = 1
FINDING_ID_RE = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")
ACCEPTANCE = ("proposed", "accepted", "dismissed")
TREND = ("unknown", "recurring", "improving", "resolved")
# 允许出现在 finding 里的字段白名单（拒绝任意文本注入）
_FIELDS = {"finding_id", "title", "scope", "recommendation", "verification",
           "evidence", "confidence", "source", "transcript_sha256"}
# evidence 只能是「已知统计名 → 程序算出的数值」。键必须来自 metrics.KNOWN_METRIC_KEYS，
# 值是有限数字；路径、命令原文、自造统计名、NaN、布尔都过不了（评审 §高2/中3）。
MAX_EVIDENCE_ITEMS = 12


def sanitize_evidence(ev, metrics=None):
    """只保留「已知统计名 → 数值」，返回 (clean, 丢弃项数)。

    metrics 不为 None 时（复盘路径）值一律取自程序本次统计，
    AI 报的数字只用于认出键名，不能覆盖程序算出的值。
    metrics 为 None 时（导入旧记录）仍要求键在已知名单内、值是有限数字。
    """
    from .metrics import KNOWN_METRIC_KEYS, is_finite_number, metric_value

    if not isinstance(ev, dict):
        return {}, 0 if not ev else 1
    clean = {}
    dropped = 0
    for k, v in ev.items():
        if not isinstance(k, str) or k not in KNOWN_METRIC_KEYS:
            dropped += 1
            continue
        if metrics is not None:
            real = metric_value(metrics, k)
            if real is None:
                dropped += 1
                continue
            clean[k] = real
        else:
            if not is_finite_number(v):
                dropped += 1
                continue
            clean[k] = v
        if len(clean) >= MAX_EVIDENCE_ITEMS:
            break
    dropped += max(0, len(ev) - len(clean) - dropped)
    return clean, dropped


def store_path():
    return os.path.expanduser("~/.cache/termcoach/findings.jsonl")


def _validate_finding(f: dict):
    fid = f.get("finding_id", "")
    if not FINDING_ID_RE.match(fid):
        raise ValueError(f"非法 finding_id: {fid!r}")
    for k in f:
        if k not in _FIELDS:
            raise ValueError(f"未知字段: {k}")
    conf = f.get("confidence")
    if conf is not None and not (isinstance(conf, (int, float)) and 0 <= conf <= 1):
        raise ValueError(f"confidence 越界: {conf}")
    for k in ("title", "recommendation", "verification"):
        v = f.get(k)
        if v is not None and (not isinstance(v, str) or len(v) > 500):
            raise ValueError(f"{k} 非法或过长")
    ev = f.get("evidence")
    if ev is not None:
        from .metrics import KNOWN_METRIC_KEYS, is_finite_number
        if not isinstance(ev, dict):
            raise ValueError("evidence 必须是 dict（统计摘要）")
        if len(ev) > MAX_EVIDENCE_ITEMS:
            raise ValueError(f"evidence 项数超过 {MAX_EVIDENCE_ITEMS}")
        for k, v in ev.items():
            if not isinstance(k, str) or k not in KNOWN_METRIC_KEYS:
                raise ValueError(f"evidence 键不是已知统计名: {k!r}")
            if not is_finite_number(v):
                raise ValueError(f"evidence[{k}] 必须是有限数值，收到 {v!r}")


def append_event(event: str, finding_id: str, path=None, **fields):
    """追加一条事件。event: proposed/accepted/dismissed/reviewed。"""
    if event not in ("proposed", "accepted", "dismissed", "reviewed"):
        raise ValueError(f"非法事件: {event}")
    if not FINDING_ID_RE.match(finding_id):
        raise ValueError(f"非法 finding_id: {finding_id!r}")
    # 纵深防御：无论调用方是否清洗过，evidence 都只留统计名+数值
    if "evidence" in fields:
        fields["evidence"] = sanitize_evidence(fields["evidence"])[0] or None
    if event == "proposed":
        _validate_finding({"finding_id": finding_id, **fields})
    if event == "reviewed":
        t = fields.get("trend")
        if t not in TREND:
            raise ValueError(f"非法 trend: {t}")
        ev = fields.get("evidence")
        if ev is not None and not isinstance(ev, dict):
            raise ValueError("evidence 必须是 dict")
    path = path or store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    rec = {"schema": SCHEMA, "event": event, "finding_id": finding_id,
           "date": time.strftime("%Y-%m-%d"), **fields}
    line = json.dumps(rec, ensure_ascii=False)
    # 单条一次写完（O_APPEND 原子性对小写入足够）；文件 600
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def load_findings(path=None) -> dict:
    """按 finding_id 合并事件流 → 当前状态 dict。"""
    path = path or store_path()
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue  # 坏行跳过，不破坏其余
            fid = ev.get("finding_id")
            if not fid or not FINDING_ID_RE.match(fid):
                continue
            kind = ev.get("event")
            cur = out.setdefault(fid, {
                "finding_id": fid, "acceptance": "proposed", "trend": "unknown",
                "times_seen": 0, "first_seen": ev.get("date"),
                "last_seen": ev.get("date"), "history": [],
            })
            cur["last_seen"] = ev.get("date", cur["last_seen"])
            if kind == "proposed":
                cur["times_seen"] += 1
                for k in _FIELDS:
                    if k in ev:
                        cur[k] = ev[k]
                cur["history"].append({"date": ev.get("date"),
                                       "evidence": ev.get("evidence")})
            elif kind in ("accepted", "dismissed"):
                cur["acceptance"] = kind
                # proposed 事件已记过一次出现，accept/dismiss 不再重复计数
                if cur["times_seen"] == 0:
                    cur["times_seen"] = 1
            elif kind == "reviewed":
                cur["trend"] = ev.get("trend", cur["trend"])
                if ev.get("evidence") is not None:
                    cur["history"].append({"date": ev.get("date"),
                                           "evidence": ev["evidence"]})
    return out


def active_findings(path=None) -> list:
    """启动提示用：accepted 且未 resolved/dismissed 的条目。"""
    return [f for f in load_findings(path).values()
            if f["acceptance"] == "accepted" and f["trend"] != "resolved"]


def _trend_lines(history: list, on=None) -> list:
    """只把「完全相同的统计键」画成一条趋势，避免跨通道串线。

    旧版汇总键（无通道前缀）与新分通道键分开成组，互不相连。
    """
    from . import style
    series = {}
    for h in history:
        for k, v in (h.get("evidence") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                series.setdefault(k, []).append(v)
    out = []
    for k, vals in series.items():
        if len(vals) < 2:
            continue
        label = k if "." in k else f"{k}（旧版汇总，与分通道值不可比）"
        out.append(style.accent(f"   {label}: " + " → ".join(str(v) for v in vals), on))
    return out[:3]


def format_tips(findings: list, on=None) -> str:
    """启动提示 / termcoach tips 的文本。on=None 时按终端能力自动决定上色。"""
    from . import style
    if not findings:
        return ""
    lines = [style.title("[termcoach] 上次待改善事项：", on), ""]
    for i, f in enumerate(findings, 1):
        lines.append(f" {style.num(str(i) + '.', on)} {style.head(f.get('title', f['finding_id']), on)}")
        # times_seen 是「被提出的复盘次数」，不是行为出现次数
        lines.append(style.dim(f"   已被提出 {f['times_seen']} 次", on)
                     + style.dim("；趋势: ", on) + style.trend(f["trend"], on))
        lines.extend(_trend_lines(f.get("history", []), on))
        if f.get("recommendation"):
            lines.append(style.dim("   本次可尝试: ", on) + f["recommendation"])
        if f.get("verification"):
            lines.append(style.dim("   验收: ", on) + f["verification"])
        lines.append("")
    return "\n".join(lines)