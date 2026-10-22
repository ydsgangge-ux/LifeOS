"""确定性检测器（第 16.4 节，P1.5）。

不用 LLM：只用 vault 里已有的行为记录做算术与排序，保证任何平台、任何次数
结果一致。每个检测器都有前置数据门槛，不足时只给 insufficient 与数据缺口；
每个输出都同时带反例（与假设相反的记录）与覆盖说明（哪些周期/领域有记录、
哪些缺失）。事实与断言的措辞由 hypothesis 层用模板生成，这里只出数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..analytics import stats
from ..db.connection import VaultConnection

# 每个检测器的最低门槛（第 16.4 节：条数与覆盖天数不足即 insufficient）
MIN_SPAN_DAYS = 60
MIN_PLANS = 4
MIN_MINUTE_RECORDS = 8
MIN_MINUTE_SPAN_DAYS = 30
MIN_ATTEMPTS = 3
MIN_REFERENCE_PLANS = 5
MIN_REFERENCE_RESOLVED = 3
MIN_CHANGE_DAYS = 14

TIME_DOMAINS = ("education", "career", "hobby")


@dataclass
class DetectorResult:
    """一个检测器的确定性输出。"""

    pattern_key: str
    sufficient: bool
    facts: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    supports: list[dict[str, Any]] = field(default_factory=list)
    counterexamples: list[dict[str, Any]] = field(default_factory=list)
    contexts: list[dict[str, Any]] = field(default_factory=list)
    gap: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "pattern_key": self.pattern_key,
            "sufficient": self.sufficient,
            "facts": self.facts,
            "coverage": self.coverage,
            "supports": self.supports,
            "counterexamples": self.counterexamples,
            "contexts": self.contexts,
            "gap": self.gap,
        }


def _fact(key: str, value: Any, unit: str | None = None) -> dict[str, Any]:
    return {"key": key, "value": value, "unit": unit}


def _ev(
    ref_type: str, ref_id: str, kind: str, note_key: str, **params: Any
) -> dict[str, Any]:
    return {
        "ref_type": ref_type,
        "ref_id": ref_id,
        "kind": kind,
        "note_key": note_key,
        "note_params": params,
    }


def _gap(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": params}


def _coverage(
    days: int, items: list[dict[str, Any]], missing: list[dict[str, Any]]
) -> dict[str, Any]:
    return {"days": days, "items": items, "missing": missing}


# ---------------------------------------------------------------------------
# start_vs_finish：我执行力强、我能坚持
# ---------------------------------------------------------------------------
def start_vs_finish(
    conn: VaultConnection, *, as_of: date, window_days: int = 180
) -> DetectorResult:
    window = timedelta(days=window_days)
    start = as_of - window
    rows = conn.query(
        "SELECT id, status, start_date, end_date, started_at, closed_at, created_at"
        " FROM plan WHERE deleted_at IS NULL AND substr(created_at, 1, 10) >= ?"
        " AND substr(created_at, 1, 10) <= ?",
        (start.isoformat(), as_of.isoformat()),
    )
    plans = [dict(r) for r in rows]
    counts = {"done": 0, "dropped": 0, "replaced": 0, "active": 0}
    done_ev: list[dict[str, Any]] = []
    drop_ev: list[dict[str, Any]] = []
    spans: list[tuple[date, date, str]] = []
    for plan in plans:
        status = str(plan["status"])
        counts[status if status in counts else "active"] += 1
        if status == "done":
            done_ev.append(_ev("plan", plan["id"], "counterexample", "selfcheck.ev.finished"))
        elif status in ("dropped", "replaced"):
            drop_ev.append(
                _ev("plan", plan["id"], "supports", "selfcheck.ev.not_finished", status=status)
            )
        first = _parse_day(plan["start_date"] or plan["started_at"] or plan["created_at"])
        last_raw = plan["end_date"] or plan["closed_at"]
        last = _parse_day(last_raw) if last_raw else as_of
        if first is not None and last is not None and last >= first:
            spans.append((first, last, str(plan["id"])))

    resolved = counts["done"] + counts["dropped"] + counts["replaced"]
    rate = (counts["done"] / resolved) if resolved else None
    span_days = _span_days(plans, as_of)
    parallel_max = _max_parallel(spans)
    parallel_weeks, done_weeks = _weekly_counts(spans, plans, start, as_of)
    n_eff = stats.effective_sample_size(parallel_weeks, done_weeks) if parallel_weeks else 0.0
    rho = (
        stats.spearman(parallel_weeks, done_weeks)
        if len(parallel_weeks) >= 6
        else None
    )

    sufficient = len(plans) >= MIN_PLANS and span_days >= MIN_SPAN_DAYS
    facts = [
        _fact("selfcheck.fact.plans_started", len(plans)),
        _fact("selfcheck.fact.plans_done", counts["done"]),
        _fact("selfcheck.fact.plans_dropped", counts["dropped"] + counts["replaced"]),
        _fact("selfcheck.fact.plans_active", counts["active"]),
        _fact("selfcheck.fact.completion_rate", _round(rate)),
        _fact("selfcheck.fact.parallel_max", parallel_max),
        _fact("selfcheck.fact.n_eff", _round(n_eff)),
        _fact("selfcheck.fact.rho", _round(rho)),
    ]
    coverage = _coverage(
        span_days,
        [
            _fact("selfcheck.cov.window_days", window_days),
            _fact("selfcheck.cov.plan_records", len(plans)),
        ],
        [] if sufficient else [_gap("selfcheck.miss.more_plan_records", min_plans=MIN_PLANS)],
    )
    return DetectorResult(
        pattern_key="start_vs_finish",
        sufficient=sufficient,
        facts=facts,
        coverage=coverage,
        supports=drop_ev,
        counterexamples=done_ev,
        contexts=[
            _ev("plan", p["id"], "context", "selfcheck.ev.still_active")
            for p in plans
            if str(p["status"]) == "active"
        ],
        gap=[]
        if sufficient
        else [_gap("selfcheck.miss.plan_span", min_days=MIN_SPAN_DAYS, days=span_days)],
    )


# ---------------------------------------------------------------------------
# time_vs_values：我最重视 X；我没有时间
# ---------------------------------------------------------------------------
def time_vs_values(
    conn: VaultConnection,
    *,
    as_of: date,
    claim_domain: str | None,
    window_days: int = 90,
) -> DetectorResult:
    start = as_of - timedelta(days=window_days)
    minutes: dict[str, int] = {}
    record_count = 0
    for domain, table in (
        ("education", "education_record"),
        ("career", "career_record"),
        ("hobby", "hobby_record"),
    ):
        row = conn.query_one(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(t.minutes), 0) AS m FROM {table} t"
            " JOIN entry e ON e.id = t.entry_id"
            " WHERE e.deleted_at IS NULL AND e.occurred_date >= ? AND e.occurred_date <= ?"
            " AND t.minutes IS NOT NULL",
            (start.isoformat(), as_of.isoformat()),
        )
        minutes[domain] = int(row["m"] or 0) if row is not None else 0
        record_count += int(row["n"] or 0) if row is not None else 0

    total_minutes = sum(minutes.values())
    span_days = _record_span_days(
        conn,
        [
            ("education_record", "e.occurred_date"),
            ("career_record", "e.occurred_date"),
            ("hobby_record", "e.occurred_date"),
        ],
        start,
        as_of,
    )
    expense_total = conn.scalar(
        "SELECT COALESCE(SUM(t.amount_minor), 0) FROM txn t JOIN entry e ON e.id = t.entry_id"
        " WHERE e.deleted_at IS NULL AND t.direction = 'out'"
        " AND e.occurred_date >= ? AND e.occurred_date <= ?",
        (start.isoformat(), as_of.isoformat()),
    )
    ranked = sorted(minutes.items(), key=lambda kv: kv[1], reverse=True)
    top_domain, top_minutes = ranked[0]
    shares = {
        domain: _round(value / total_minutes if total_minutes else None)
        for domain, value in minutes.items()
    }

    sufficient = (
        record_count >= MIN_MINUTE_RECORDS
        and span_days >= MIN_MINUTE_SPAN_DAYS
        and claim_domain in TIME_DOMAINS
    )
    facts = [
        _fact("selfcheck.fact.minutes_education", minutes["education"]),
        _fact("selfcheck.fact.minutes_career", minutes["career"]),
        _fact("selfcheck.fact.minutes_hobby", minutes["hobby"]),
        _fact("selfcheck.fact.share_education", shares["education"]),
        _fact("selfcheck.fact.share_career", shares["career"]),
        _fact("selfcheck.fact.share_hobby", shares["hobby"]),
        _fact("selfcheck.fact.top_domain", top_domain),
        _fact("selfcheck.fact.expense_total_minor", int(expense_total or 0)),
    ]
    missing = [
        _gap("selfcheck.miss.expense_attribution"),
        _gap("selfcheck.miss.unclassified_time"),
    ]
    if claim_domain not in TIME_DOMAINS:
        missing.append(_gap("selfcheck.miss.no_value_claim"))
    if record_count < MIN_MINUTE_RECORDS:
        missing.append(_gap("selfcheck.miss.more_time_records", min_records=MIN_MINUTE_RECORDS))
    supports = []
    counterexamples = []
    if claim_domain in TIME_DOMAINS:
        claim_minutes = minutes[claim_domain]
        if top_domain != claim_domain and top_minutes > claim_minutes:
            counterexamples.append(
                _ev(
                    "metric",
                    f"minutes.{top_domain}",
                    "counterexample",
                    "selfcheck.ev.top_not_claim",
                    domain=top_domain,
                )
            )
            supports.append(
                _ev(
                    "metric",
                    f"minutes.{claim_domain}",
                    "supports",
                    "selfcheck.ev.claim_domain_gap",
                )
            )
    return DetectorResult(
        pattern_key="time_vs_values",
        sufficient=sufficient,
        facts=facts,
        coverage=_coverage(span_days, [_fact("selfcheck.cov.time_records", record_count)], missing),
        supports=supports,
        counterexamples=counterexamples,
        contexts=[],
        gap=[] if sufficient else missing,
    )


# ---------------------------------------------------------------------------
# ability_vs_trial：我不擅长 X
# ---------------------------------------------------------------------------
def ability_vs_trial(
    conn: VaultConnection, *, as_of: date, skill_id: str | None
) -> DetectorResult:
    if not skill_id:
        g = _gap("selfcheck.miss.no_skill")
        return DetectorResult(
            pattern_key="ability_vs_trial",
            sufficient=False,
            coverage=_coverage(0, [], [g]),
            gap=[g],
        )
    assessments = conn.query(
        "SELECT level, assessed_at FROM skill_level WHERE skill_id = ? ORDER BY assessed_at",
        (skill_id,),
    )
    attempts = conn.query(
        "SELECT e.id FROM entry e WHERE e.deleted_at IS NULL AND e.id IN ("
        "  SELECT entry_id FROM education_record WHERE skill_id = ?"
        "  UNION SELECT entry_id FROM career_record WHERE skill_id = ?)",
        (skill_id, skill_id),
    )
    minutes_row = conn.query_one(
        "SELECT COALESCE(SUM(t.minutes), 0) AS m FROM ("
        "  SELECT minutes FROM education_record WHERE skill_id = ?"
        "  UNION ALL SELECT minutes FROM career_record WHERE skill_id = ?) AS t",
        (skill_id, skill_id),
    )
    total_minutes = int(minutes_row["m"] or 0) if minutes_row is not None else 0
    levels = [float(r["level"]) for r in assessments]
    slope = None
    if len(levels) >= 3:
        fit = stats.linear_slope(list(range(len(levels))), levels)
        slope = None if fit is None else _round(fit.get("slope"))
    attempt_n = len(attempts)
    sufficient = attempt_n >= MIN_ATTEMPTS
    facts = [
        _fact("selfcheck.fact.attempts", attempt_n),
        _fact("selfcheck.fact.total_minutes", total_minutes),
        _fact("selfcheck.fact.level_first", _round(levels[0] if levels else None)),
        _fact("selfcheck.fact.level_last", _round(levels[-1] if levels else None)),
        _fact("selfcheck.fact.level_assessments", len(levels)),
        _fact("selfcheck.fact.level_slope", slope),
    ]
    gap = [] if sufficient else [_gap("selfcheck.miss.too_few_attempts", min_attempts=MIN_ATTEMPTS)]
    supports = [_ev("entry", str(a["id"]), "supports", "selfcheck.ev.attempt") for a in attempts]
    counterexamples = []
    if slope is not None and slope > 0:
        counterexamples.append(
            _ev("metric", f"skill.{skill_id}.slope", "counterexample", "selfcheck.ev.level_up")
        )
    return DetectorResult(
        pattern_key="ability_vs_trial",
        sufficient=sufficient,
        facts=facts,
        coverage=_coverage(0, [_fact("selfcheck.cov.attempts", attempt_n)], gap),
        supports=supports,
        counterexamples=counterexamples,
        contexts=[],
        gap=gap,
    )


# ---------------------------------------------------------------------------
# forecast_vs_history：这个项目会成功
# ---------------------------------------------------------------------------
def forecast_vs_history(
    conn: VaultConnection, *, as_of: date, area: str | None
) -> DetectorResult:
    if not area:
        g = _gap("selfcheck.miss.no_area")
        return DetectorResult(
            pattern_key="forecast_vs_history",
            sufficient=False,
            coverage=_coverage(0, [], [g]),
            gap=[g],
        )
    rows = conn.query(
        "SELECT p.id, p.status FROM plan p JOIN goal g ON g.id = p.goal_id"
        " WHERE p.deleted_at IS NULL AND g.area = ? AND substr(p.created_at, 1, 10) <= ?",
        (area, as_of.isoformat()),
    )
    sample = len(rows)
    done = sum(1 for r in rows if str(r["status"]) == "done")
    dropped = sum(1 for r in rows if str(r["status"]) in ("dropped", "replaced"))
    resolved = done + dropped
    base_rate = (done / resolved) if resolved else None
    sufficient = sample >= MIN_REFERENCE_PLANS and resolved >= MIN_REFERENCE_RESOLVED
    facts = [
        _fact("selfcheck.fact.reference_n", sample),
        _fact("selfcheck.fact.reference_done", done),
        _fact("selfcheck.fact.reference_dropped", dropped),
        _fact("selfcheck.fact.base_rate", _round(base_rate)),
    ]
    gap = (
        []
        if sufficient
        else [_gap("selfcheck.miss.thin_reference", min_plans=MIN_REFERENCE_PLANS, n=sample)]
    )
    supports = [
        _ev("plan", str(r["id"]), "supports", "selfcheck.ev.reference_dropped")
        for r in rows
        if str(r["status"]) in ("dropped", "replaced")
    ]
    counterexamples = [
        _ev("plan", str(r["id"]), "counterexample", "selfcheck.ev.reference_done")
        for r in rows
        if str(r["status"]) == "done"
    ]
    return DetectorResult(
        pattern_key="forecast_vs_history",
        sufficient=sufficient,
        facts=facts,
        coverage=_coverage(0, [_fact("selfcheck.cov.reference_plans", sample)], gap),
        supports=supports,
        counterexamples=counterexamples,
        contexts=[],
        gap=gap,
    )


# ---------------------------------------------------------------------------
# change_claim：我已经改变了
# ---------------------------------------------------------------------------
_METRIC_SQL: dict[str, tuple[str, str]] = {
    "daily.mood.mean": ("daily_log", "mood"),
    "daily.stress.mean": ("daily_log", "stress"),
    "daily.energy.mean": ("daily_log", "energy"),
    "health.sleep.mean_hours": ("health_log", "sleep_hours"),
    "health.exercise.minutes_total": ("health_log", "exercise_minutes"),
}


def change_claim(
    conn: VaultConnection, *, as_of: date, metric_key: str | None, window_days: int = 90
) -> DetectorResult:
    if metric_key not in _METRIC_SQL:
        g = _gap("selfcheck.miss.unsupported_metric", metric=metric_key or "")
        return DetectorResult(
            pattern_key="change_claim",
            sufficient=False,
            coverage=_coverage(0, [], [g]),
            gap=[g],
        )
    table, column = _METRIC_SQL[metric_key]
    split = as_of - timedelta(days=window_days)
    start = as_of - timedelta(days=window_days * 2)
    rows = conn.query(
        f"SELECT e.occurred_date AS d, AVG(t.{column}) AS v FROM {table} t"
        " JOIN entry e ON e.id = t.entry_id"
        f" WHERE e.deleted_at IS NULL AND t.{column} IS NOT NULL"
        " AND e.occurred_date >= ? AND e.occurred_date <= ?"
        " GROUP BY e.occurred_date ORDER BY e.occurred_date",
        (start.isoformat(), as_of.isoformat()),
    )
    series = [(date.fromisoformat(str(r["d"])[:10]), float(r["v"])) for r in rows]
    prev = [v for d, v in series if d <= split]
    recent = [v for d, v in series if d > split]
    sufficient = len(prev) >= MIN_CHANGE_DAYS and len(recent) >= MIN_CHANGE_DAYS
    prev_mean = stats.mean(prev)
    recent_mean = stats.mean(recent)
    diff = (
        (recent_mean - prev_mean)
        if prev_mean is not None and recent_mean is not None
        else None
    )
    spread = stats.sample_std([v for _, v in series])
    within = None
    if diff is not None and spread:
        within = abs(diff) < spread
    consistency = _direction_consistency(series, diff)
    facts = [
        _fact("selfcheck.fact.prev_mean", _round(prev_mean)),
        _fact("selfcheck.fact.recent_mean", _round(recent_mean)),
        _fact("selfcheck.fact.diff", _round(diff)),
        _fact("selfcheck.fact.spread", _round(spread)),
        _fact("selfcheck.fact.consistency", _round(consistency)),
        _fact("selfcheck.fact.within_fluctuation", within),
        _fact("selfcheck.fact.window_days", window_days),
    ]
    gap = (
        []
        if sufficient
        else [
            _gap(
                "selfcheck.miss.thin_change_series",
                min_days=MIN_CHANGE_DAYS,
                prev=len(prev),
                recent=len(recent),
            )
        ]
    )
    supports: list[dict[str, Any]] = []
    counterexamples: list[dict[str, Any]] = []
    if consistency is not None and diff is not None:
        aligned = consistency >= 0.6
        note = (
            "selfcheck.ev.change_consistent"
            if aligned
            else "selfcheck.ev.change_inconsistent"
        )
        (supports if aligned else counterexamples).append(
            _ev("metric", metric_key, "supports" if aligned else "counterexample", note)
        )
    return DetectorResult(
        pattern_key="change_claim",
        sufficient=sufficient,
        facts=facts,
        coverage=_coverage(len(series), [
            _fact("selfcheck.cov.prev_days", len(prev)),
            _fact("selfcheck.cov.recent_days", len(recent)),
        ], gap),
        supports=supports,
        counterexamples=counterexamples,
        contexts=[],
        gap=gap,
    )


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _parse_day(value: Any) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _span_days(plans: list[dict[str, Any]], as_of: date) -> int:
    earliest: date | None = None
    for plan in plans:
        first = _parse_day(plan["created_at"])
        if first is not None and (earliest is None or first < earliest):
            earliest = first
    if earliest is None:
        return 0
    return max(0, (as_of - earliest).days)


def _record_span_days(
    conn: VaultConnection, tables: list[tuple[str, str]], start: date, as_of: date
) -> int:
    earliest: date | None = None
    for table, column in tables:
        row = conn.query_one(
            f"SELECT MIN({column}) AS d FROM {table} t JOIN entry e ON e.id = t.entry_id"
            " WHERE e.deleted_at IS NULL AND e.occurred_date >= ? AND e.occurred_date <= ?",
            (start.isoformat(), as_of.isoformat()),
        )
        day = _parse_day(row["d"]) if row is not None else None
        if day is not None and (earliest is None or day < earliest):
            earliest = day
    if earliest is None:
        return 0
    return max(0, (as_of - earliest).days)


def _max_parallel(spans: list[tuple[date, date, str]]) -> int:
    events: list[tuple[date, int]] = []
    for first, last, _ in spans:
        events.append((first, 1))
        events.append((last + timedelta(days=1), -1))
    events.sort()
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def _weekly_counts(
    spans: list[tuple[date, date, str]],
    plans: list[dict[str, Any]],
    start: date,
    as_of: date,
) -> tuple[list[float], list[float]]:
    """按周聚合：每周并行计划数与每周完成计划数（给相关用）。"""
    weeks = max(1, (as_of - start).days // 7)
    parallel = [0.0] * weeks
    for first, last, _ in spans:
        lo = max(0, (first - start).days // 7)
        hi = min(weeks - 1, (last - start).days // 7)
        for w in range(lo, hi + 1):
            parallel[w] += 1.0
    done = [0.0] * weeks
    for plan in plans:
        if str(plan["status"]) != "done":
            continue
        closed = _parse_day(plan["closed_at"] or plan["end_date"])
        if closed is None:
            continue
        idx = (closed - start).days // 7
        if 0 <= idx < weeks:
            done[idx] += 1.0
    return parallel, done


def _direction_consistency(
    series: list[tuple[date, float]], diff: float | None
) -> float | None:
    """方向一致的周数占比（第 16.4 节 change_claim 输出之一）。"""
    if diff is None or diff == 0 or len(series) < 3:
        return None
    buckets: dict[int, list[float]] = {}
    base = series[0][0]
    for day, value in series:
        buckets.setdefault((day - base).days // 7, []).append(value)
    keys = sorted(buckets)
    weekly = [sum(buckets[k]) / len(buckets[k]) for k in keys]
    deltas = [weekly[i + 1] - weekly[i] for i in range(len(weekly) - 1)]
    if not deltas:
        return None
    sign = 1.0 if diff > 0 else -1.0
    matched = sum(1 for d in deltas if d != 0 and (d > 0) == (sign > 0))
    return matched / len(deltas)


def _round(value: Any, digits: int = 4) -> Any:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value
