"""导入解析与证据校验（第 10.5 节）。

用户把网页版模型的回答粘贴到对应 research_task。解析器只抽取结构化字段写入
evidence 表，**不整块转发原文**。粘贴内容一律视为数据，任何形如指令的文字
都只被当成 claim 文本处理，不会被当作系统指令执行（第 7.8 节提示词注入防护）。

自动校验：
- 缺来源或 URL → confidence=low，界面标「待核实」
- 发布时间早于时效阈值 → stale=1
- 同一问题下数值相对差 > 20% → 互相写入 conflicts_with
- 按域名归类来源类型
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..domain.decisions import DecisionService

SEP = "｜"
# 一条证据字段：结论｜关键数据｜来源名称｜来源URL｜发布时间｜证据类型｜冲突
_FIELD_COUNT = 7

_OFFICIAL_DOMAINS = ("gov.cn", "edu.cn", "stats.gov", "gov", "edu")
_RESEARCH_HINTS = ("论文", "期刊", "研究", "学报", "arxiv", "sci")

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _host_of(url: str | None) -> str:
    if not url:
        return ""
    # 去掉 scheme，取 // 之后到第一个 / 之前的部分
    rest = url.split("//", 1)[-1]
    return rest.split("/", 1)[0].split(":")[0].lower()


def _classify_source_type(
    source_url: str | None, source_name: str | None, ev_type: str | None
) -> str:
    """按域名/名称归类来源类型。"""
    if ev_type:
        ev = ev_type.strip().lower()
        if ev in ("官方统计", "官方"):
            return "official"
        if ev in ("研究", "学术", "论文"):
            return "research"
        if ev in ("媒体",):
            return "media"
        if ev in ("个人经验",):
            return "experience"
    combined = f"{source_url or ''} {source_name or ''}".lower()
    host = _host_of(source_url)
    for domain in _OFFICIAL_DOMAINS:
        if domain in host or domain in combined:
            return "official"
    if any(h in combined for h in _RESEARCH_HINTS):
        return "research"
    if source_url and host:
        return "media"
    return "unknown"


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.search(r"(20\d{2})", text)
    if match:
        try:
            return date(int(match.group(1)), 1, 1)
        except ValueError:
            return None
    return None


def _parse_line(line: str) -> dict[str, Any] | None:
    """解析一行「｜」分隔的证据。失败返回 None。"""
    parts = [p.strip() for p in line.split(SEP)]
    if len(parts) < 2:
        return None
    claim = parts[0]
    if not claim:
        return None
    data_value = parts[1] if len(parts) > 1 else None
    source_name = parts[2] if len(parts) > 2 else None
    source_url = parts[3] if len(parts) > 3 else None
    source_date = parts[4] if len(parts) > 4 else None
    ev_type = parts[5] if len(parts) > 5 else None
    conflicts = parts[6] if len(parts) > 6 else None
    return {
        "claim": claim,
        "data_value": data_value or None,
        "source_name": source_name or None,
        "source_url": source_url or None,
        "source_date": source_date or None,
        "evidence_type": ev_type or None,
        "conflicts_note": conflicts or None,
    }


def _numeric_value(text: str | None) -> float | None:
    if not text:
        return None
    match = _NUM_RE.search(text)
    return float(match.group(0)) if match else None


def import_pasted(
    decisions: DecisionService,
    task_id: str,
    pasted_text: str,
    *,
    freshness_days: int = 365,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """把粘贴内容解析成 evidence 并落库。

    任何内容都只被当作数据字段，不会被执行。无法结构化的行被丢弃（不写入），
    避免把恶意指令送进后续模型上下文。
    """
    today = today or date.today()
    # 先落库粘贴文本（便于追溯），状态置 pasted
    decisions.set_task_pasted(task_id, pasted_text)

    parsed: list[dict[str, Any]] = []
    for raw_line in pasted_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        item = _parse_line(line)
        if item is not None:
            parsed.append(item)

    # 数值冲突检测：同一问题下数值差 > 20% 互相标记
    numeric_pairs: list[tuple[int, float]] = []
    for i, p in enumerate(parsed):
        v = _numeric_value(p["data_value"])
        if v is not None:
            numeric_pairs.append((i, v))
    conflicts: dict[int, list[int]] = {}
    for idx_a, val_a in numeric_pairs:
        for idx_b, val_b in numeric_pairs:
            if idx_a >= idx_b:
                continue
            if val_a == 0 and val_b == 0:
                continue
            denom = max(abs(val_a), abs(val_b))
            if denom == 0:
                continue
            rel_diff = abs(val_a - val_b) / denom
            if rel_diff > 0.2:
                conflicts.setdefault(idx_a, []).append(idx_b)
                conflicts.setdefault(idx_b, []).append(idx_a)

    created: list[dict[str, Any]] = []
    for i, item in enumerate(parsed):
        has_source = bool(item["source_name"] or item["source_url"])
        src_date = _parse_date(item["source_date"])
        stale = False
        if src_date is not None:
            stale = (today - src_date).days > freshness_days
        source_type = _classify_source_type(
            item["source_url"], item["source_name"], item["evidence_type"]
        )
        confidence = "high" if has_source and not stale else "medium" if has_source else "low"
        conflict_ids = conflicts.get(i, [])
        conflicts_with = str(len(conflict_ids)) if conflict_ids else None
        evidence = decisions.add_evidence(
            task_id,
            claim=item["claim"],
            data_value=item["data_value"],
            source_name=item["source_name"],
            source_url=item["source_url"],
            source_date=src_date.isoformat() if src_date else None,
            source_type=source_type,
            confidence=confidence,
            conflicts_with=conflicts_with,
            stale=stale,
        )
        created.append(evidence)

    # 任务状态推进到 ingested
    decisions.set_task_status(task_id, "ingested")
    return created


def evidence_weight(evidence: dict[str, Any]) -> float:
    """合成时的证据权重（第 10.5、10.6 节）。

    未核实（user_verified=False）一律降权；confidence 与 source_type 决定基础权重；
    stale 再降权。返回 0.0~1.0。
    """
    base = {
        "official": 1.0,
        "research": 0.9,
        "media": 0.6,
        "experience": 0.4,
        "unknown": 0.3,
    }.get(evidence.get("source_type", "unknown"), 0.3)
    conf_mult = {"high": 1.0, "medium": 0.7, "low": 0.4}.get(evidence.get("confidence", "low"), 0.4)
    weight = base * conf_mult
    if not evidence.get("user_verified", False):
        weight *= 0.5  # 未核实降权
    if evidence.get("stale", False):
        weight *= 0.6
    return round(weight, 3)
