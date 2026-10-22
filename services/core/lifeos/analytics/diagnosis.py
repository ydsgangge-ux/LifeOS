"""计划诊断器（第 8.6 节）：对每个活跃计划按固定顺序命中 8 条规则。

- 判定窗口 W4（最近 4 周）用于规则 3、4、6、7、8；历史窗口 W8（最近 8 周）
  只用于规则 5，历史不足 8 周时规则 5 不触发。
- 完成率保留全部记录，不因意外而排除（否则失败被排除后完成率会虚高）；
  意外只通过意外占比单列（ADR-13）。
- 阈值全部来自 config/diagnosis.yaml，每份报告附上本次使用的阈值快照。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from ..db.connection import VaultConnection
from ..domain.errors import InvalidInputError
from ..i18n import message
from . import trends


@dataclass(frozen=True)
class DiagnosisConfig:
    version: str
    verdict_weeks: int
    history_weeks: int
    completion_low: float
    anomaly_share_high: float
    min_span_weeks: int
    min_records: int
    flat_weeks: int
    near_zero_weeks: int
    opportunity_minutes_per_week: int

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "verdict_weeks": self.verdict_weeks,
            "history_weeks": self.history_weeks,
            "completion_low": self.completion_low,
            "anomaly_share_high": self.anomaly_share_high,
            "min_span_weeks": self.min_span_weeks,
            "min_records": self.min_records,
            "flat_weeks": self.flat_weeks,
            "near_zero_weeks": self.near_zero_weeks,
            "opportunity_minutes_per_week": self.opportunity_minutes_per_week,
        }


def load_diagnosis_config(path: str | Path) -> DiagnosisConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    windows = raw.get("windows", {})
    completion = raw.get("completion", {})
    anomaly = raw.get("anomaly", {})
    suff = raw.get("data_sufficiency", {})
    opportunity = raw.get("opportunity", {})
    try:
        return DiagnosisConfig(
            version=str(raw.get("version", "diag_v1")),
            verdict_weeks=int(windows.get("verdict_weeks", 4)),
            history_weeks=int(windows.get("history_weeks", 8)),
            completion_low=float(completion.get("low", 0.7)),
            anomaly_share_high=float(anomaly.get("share_high", 0.5)),
            min_span_weeks=int(suff.get("min_span_weeks", 3)),
            min_records=int(suff.get("min_records", 6)),
            flat_weeks=int(suff.get("flat_weeks", 4)),
            near_zero_weeks=int(suff.get("near_zero_weeks", 8)),
            opportunity_minutes_per_week=int(opportunity.get("minutes_per_week_high", 240)),
        )
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"诊断阈值配置非法：{path}") from exc


def _plan_records(
    conn: VaultConnection, plan_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    sql = (
        "SELECT e.id AS entry_id, e.occurred_date AS d, pr.planned_units AS planned,"
        " pr.actual_units AS actual, pr.output_value AS output, pr.outcome AS outcome,"
        " (SELECT COUNT(*) FROM anomaly a WHERE a.entry_id = e.id) AS anomaly_n"
        " FROM plan_record pr JOIN entry e ON e.id = pr.entry_id"
        " WHERE pr.plan_id = ? AND e.deleted_at IS NULL AND e.status = 'final'"
        " AND e.occurred_date >= ? AND e.occurred_date <= ?"
        " ORDER BY e.occurred_date, e.id"
    )
    rows = conn.query(sql, (plan_id, start.isoformat(), end.isoformat()))
    return [
        {
            "entry_id": r["entry_id"],
            "date": str(r["d"])[:10],
            "planned": r["planned"],
            "actual": r["actual"],
            "output": r["output"],
            "outcome": r["outcome"],
            "anomaly_n": int(r["anomaly_n"]),
        }
        for r in rows
    ]


def _completion_rate(rows: list[dict[str, Any]]) -> float | None:
    planned = sum(float(r["planned"]) for r in rows if r["planned"] is not None)
    actual = sum(float(r["actual"]) for r in rows if r["actual"] is not None)
    if planned <= 0:
        return None
    return actual / planned


def _anomaly_share(rows: list[dict[str, Any]]) -> float:
    incomplete = [r for r in rows if r["outcome"] != "done"]
    if not incomplete:
        return 0.0
    flagged = sum(1 for r in incomplete if r["anomaly_n"] > 0)
    return flagged / len(incomplete)


def _output_weekly(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    by_date: dict[str, float] = {}
    for r in rows:
        value = r["output"] if r["output"] is not None else r["actual"]
        if value is None:
            continue
        by_date[r["date"]] = by_date.get(r["date"], 0.0) + float(value)
    buckets: dict[str, list[float]] = {}
    for date_str, value in by_date.items():
        d = date.fromisoformat(date_str)
        monday = d - timedelta(days=d.weekday())
        buckets.setdefault(monday.isoformat(), []).append(value)
    return [
        {"week_start": ws, "value": sum(buckets[ws]), "n": len(buckets[ws])}
        for ws in sorted(buckets)
    ]


def _weeks_with_incomplete(rows: list[dict[str, Any]]) -> int:
    weeks = set()
    for r in rows:
        if r["outcome"] != "done":
            d = date.fromisoformat(r["date"])
            weeks.add((d - timedelta(days=d.weekday())).isoformat())
    return len(weeks)


def diagnose_plan(
    conn: VaultConnection,
    plan: dict[str, Any],
    cfg: DiagnosisConfig,
    *,
    as_of: date,
) -> dict[str, Any]:
    plan_id = plan["id"]
    start4 = as_of - timedelta(days=cfg.verdict_weeks * 7 - 1)
    start8 = as_of - timedelta(days=cfg.history_weeks * 7 - 1)
    w4 = _plan_records(conn, plan_id, start4, as_of)
    w8 = _plan_records(conn, plan_id, start8, as_of)
    all_rows = _plan_records(conn, plan_id, date(1900, 1, 1), as_of)

    span_weeks = 0.0
    if len(all_rows) >= 2:
        first = date.fromisoformat(all_rows[0]["date"])
        last = date.fromisoformat(all_rows[-1]["date"])
        span_weeks = (last - first).days / 7.0

    cr4 = _completion_rate(w4)
    share4 = _anomaly_share(w4)
    trend4 = trends.trend_direction(_output_weekly(w4))
    run4 = trends.non_increasing_run(_output_weekly(w4))
    weeks4 = len(_output_weekly(w4))
    run8_near_zero = trends.near_zero_run(_output_weekly(w8), threshold=0.0)
    weeks_incomplete = _weeks_with_incomplete(w4)

    goal = None
    latest_decision = None
    if plan.get("goal_id"):
        goal = conn.query_one("SELECT * FROM goal WHERE id = ?", (plan["goal_id"],))
        checkpoint = conn.query_one(
            "SELECT gc.decision AS decision FROM goal_checkpoint gc"
            " JOIN entry e ON e.id = gc.entry_id"
            " WHERE gc.goal_id = ? AND e.deleted_at IS NULL"
            " ORDER BY e.occurred_date DESC, e.id DESC LIMIT 1",
            (plan["goal_id"],),
        )
        if checkpoint is not None:
            latest_decision = checkpoint["decision"]

    # 理由以「可本地化的消息对象」返回（NFR-08）：{key, params}，
    # 由渲染层（前端 i18next / 报告模板）按当前语言出文案。
    reasons: list[dict[str, Any]] = []
    verdict = "watch"

    if span_weeks < cfg.min_span_weeks or len(all_rows) < cfg.min_records:
        verdict = "insufficient_data"
        reasons.append(
            message(
                "diag.insufficient",
                min_span=cfg.min_span_weeks,
                min_records=cfg.min_records,
                span=f"{span_weeks:.1f}",
                records=len(all_rows),
            )
        )
    else:
        goal_status = goal["status"] if goal is not None else None
        goal_flag = goal_status in ("dropped", "paused", "revised")
        decision_flag = latest_decision in ("drop", "pause", "revise")
        if goal_flag or decision_flag:
            verdict = "goal_review"
            if goal_flag:
                reasons.append(message("diag.goal_status", status=goal_status))
            if decision_flag:
                reasons.append(message("diag.goal_decision", decision=latest_decision))
            reasons.append(message("diag.goal_review_hint"))
        elif cr4 is not None and cr4 >= cfg.completion_low and trend4 == "up":
            verdict = "keep"
            reasons.append(
                message("diag.keep", cr=f"{cr4:.2f}", low=cfg.completion_low)
            )
        elif (
            cr4 is not None
            and cr4 >= cfg.completion_low
            and trend4 in ("flat", "down")
            and weeks4 >= cfg.flat_weeks
        ):
            verdict = "redesign"
            reasons.append(
                message("diag.redesign_flat", cr=f"{cr4:.2f}", weeks=run4)
            )
        elif (
            span_weeks >= cfg.history_weeks
            and run8_near_zero >= cfg.near_zero_weeks
            and _goal_link_weak(plan, goal, cfg)
        ):
            verdict = "retire"
            reasons.append(message("diag.retire", weeks=run8_near_zero))
        elif cr4 is not None and cr4 < cfg.completion_low and share4 >= cfg.anomaly_share_high:
            verdict = "catchup"
            reasons.append(
                message(
                    "diag.catchup",
                    cr=f"{cr4:.2f}",
                    low=cfg.completion_low,
                    share=f"{share4:.2f}",
                    high=cfg.anomaly_share_high,
                )
            )
        elif (
            cr4 is not None
            and cr4 < cfg.completion_low
            and share4 == 0.0
            and weeks_incomplete >= 3
        ):
            verdict = "redesign"
            reasons.append(
                message(
                    "diag.redesign_no_anomaly",
                    cr=f"{cr4:.2f}",
                    low=cfg.completion_low,
                    weeks=weeks_incomplete,
                )
            )
        else:
            verdict = "watch"
            reasons.append(message("diag.watch"))

    reject = _verdict_reject(conn, plan_id, as_of, cfg)
    if reject is not None:
        reasons.append(message("diag.rejected"))

    return {
        "plan_id": plan_id,
        "ref": plan.get("ref"),
        "verdict": verdict,
        "reasons": reasons,
        "thresholds_ref": cfg.version,
        "thresholds": cfg.snapshot(),
        "completion_rate": cr4,
        "anomaly_share": share4,
        "output_trend": trend4,
        "span_weeks": round(span_weeks, 2),
        "records": len(all_rows),
        "user_rejected": reject is not None,
        "suppressed_until": reject,
        "sunk_cost_question": (
            message("diag.sunk_cost") if verdict == "retire" else None
        ),
    }


def _goal_link_weak(plan: dict[str, Any], goal: Any, cfg: DiagnosisConfig) -> bool:
    if plan.get("goal_id") is None or goal is None or goal["status"] != "active":
        return True
    est = plan.get("est_minutes_per_week")
    return bool(est is not None and int(est) >= cfg.opportunity_minutes_per_week)


def _verdict_reject(
    conn: VaultConnection, plan_id: str, as_of: date, cfg: DiagnosisConfig
) -> str | None:
    """返回 4 周内的抑制截止日；没有驳回纠正则返回 None（第 8.10 节）。"""
    cutoff = (as_of - timedelta(weeks=4)).isoformat()
    row = conn.query_one(
        "SELECT created_at FROM user_correction WHERE kind = 'verdict_reject'"
        " AND target_type = 'diagnosis' AND target_id = ? AND revoked_at IS NULL"
        " AND substr(created_at, 1, 10) >= ? ORDER BY created_at DESC LIMIT 1",
        (plan_id, cutoff),
    )
    if row is None:
        return None
    reject_date = date.fromisoformat(str(row["created_at"])[:10])
    return (reject_date + timedelta(weeks=4)).isoformat()
