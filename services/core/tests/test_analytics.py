"""分析层测试（第 8 章、第 14 章 P1 analytics 验收清单）。

覆盖：
- 指标手算小样本核对（均值 / 求和 / 公式 / 金额 / 意外排除双值 / 血缘）。
- 诊断器 8 条规则与边界，全部参数化。
- 相关性：植入已知相关可被检出、纯噪声不产生提示、覆盖率不足记缺口、
  同一数据两次分析结果完全一致（确定性）。
- 事实包幂等：同一数据两次 facts JSON 完全一致。
- 4 个分析端点的契约（含未解锁 423 与 explain 反查）。

为跑得快，沿用 conftest 的「快速保险库」（直接给定 DEK）；数据用领域服务
或直接 SQL 构造，时间一律显式指定，保证结果可手算、可复现。
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import FastVault, build_vault
from fastapi.testclient import TestClient

from lifeos.analytics import AnalyticsService, stats
from lifeos.analytics import periods as P
from lifeos.analytics.correlation import analyze, load_correlation_config
from lifeos.analytics.diagnosis import diagnose_plan, load_diagnosis_config
from lifeos.analytics.facts import build_entry_texts
from lifeos.analytics.metrics import MetricEngine
from lifeos.analytics.registry import MetricRegistry
from lifeos.api import API_PREFIX, create_app
from lifeos.config import AppConfig
from lifeos.db.registry import ProfileRecord
from lifeos.domain.profiles import UnlockedProfile
from lifeos.util.ids import new_ulid, now_utc_iso

AS_OF = date(2026, 10, 4)  # 周日：W4 恰好对齐到 4 个自然周（周一为起点）
WEEK_MONDAYS = ("2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28")


# ---------------------------------------------------------------------------
# 构造数据的小工具
# ---------------------------------------------------------------------------
def _entry(vault: FastVault, domain: str, when: str, detail: dict) -> dict:
    return vault.service.entries.create(
        {
            "domain": domain,
            "occurred_at": f"{when}T12:00:00+08:00",
            "detail": detail,
        }
    )


def _daily(vault: FastVault, day: str, mood: int | None = None) -> dict:
    detail = {}
    if mood is not None:
        detail["mood"] = mood
    return _entry(vault, "daily", day, detail)


def _health(vault: FastVault, day: str, *, sleep: float | None = None) -> dict:
    detail = {}
    if sleep is not None:
        detail["sleep_hours"] = sleep
    return _entry(vault, "health", day, detail)


def _expense(vault: FastVault, day: str, amount_minor: int, *, currency: str = "CNY") -> dict:
    return _entry(
        vault,
        "expense",
        day,
        {"amount_minor": amount_minor, "currency": currency, "direction": "out"},
    )


def _make_goal(conn, goal_id: str, *, status: str = "active") -> str:
    now = now_utc_iso()
    conn.execute(
        "INSERT INTO goal (id, title, direction, status, created_at, updated_at)"
        " VALUES (?, ?, 'increase', ?, ?, ?)",
        (goal_id, "目标", status, now, now),
    )
    return goal_id


def _make_plan(conn, plan_id: str, *, goal_id: str | None = None, est: int | None = None) -> str:
    now = now_utc_iso()
    conn.execute(
        "INSERT INTO plan (id, goal_id, title, est_minutes_per_week, created_at, updated_at)"
        " VALUES (?, ?, '计划', ?, ?, ?)",
        (plan_id, goal_id, est, now, now),
    )
    return plan_id


def _plan_record(
    conn,
    plan_id: str,
    day: str,
    *,
    planned: float,
    actual: float,
    output: float | None,
    outcome: str,
) -> str:
    entry_id = new_ulid()
    now = now_utc_iso()
    conn.execute(
        "INSERT INTO entry (id, domain, occurred_at, occurred_date, title, status,"
        " created_at, updated_at) VALUES (?, 'plan', ?, ?, '打卡', 'final', ?, ?)",
        (entry_id, f"{day}T12:00:00+08:00", day, now, now),
    )
    conn.execute(
        "INSERT INTO plan_record (entry_id, plan_id, planned_units, actual_units,"
        " output_value, outcome) VALUES (?, ?, ?, ?, ?, ?)",
        (entry_id, plan_id, planned, actual, output, outcome),
    )
    return entry_id


def _flag_anomaly(vault: FastVault, entry_id: str) -> None:
    vault.service.entries.add_anomaly({"type": "work", "impact": 3}, entry_id)


def _engine(vault: FastVault, tmp_path: Path) -> tuple[MetricEngine, AppConfig]:
    config = AppConfig(data_dir=tmp_path / "lifeos")
    registry = MetricRegistry(config.metrics_dir)
    engine = MetricEngine(vault.conn, registry)
    return engine, config


# ---------------------------------------------------------------------------
# 指标：手算小样本核对
# ---------------------------------------------------------------------------
def test_metric_mean_hand_calculated(fast_vault):
    for day, mood in (("2026-09-28", 2), ("2026-09-29", 4), ("2026-09-30", 3), ("2026-10-01", 5)):
        _daily(fast_vault, day, mood)
    engine, _ = _engine(fast_vault, Path(fast_vault.path.parent))
    result = engine.compute("daily.mood.mean", date(2026, 9, 28), date(2026, 10, 4))
    assert result["status"] == "ok"
    assert result["value"] == pytest.approx((2 + 4 + 3 + 5) / 4)
    assert result["n"] == 4
    assert result["lineage"]["source"] == "daily_log.mood"


def test_metric_insufficient_below_min_n(fast_vault):
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        _daily(fast_vault, day, 3)
    engine, _ = _engine(fast_vault, Path(fast_vault.path.parent))
    result = engine.compute("daily.mood.mean", date(2026, 9, 28), date(2026, 10, 4))
    # min_n = 4，只有 3 条
    assert result["status"] == "insufficient_data"
    assert result["value"] is None


def test_metric_sum_and_money(fast_vault):
    _expense(fast_vault, "2026-09-28", 2800)
    _expense(fast_vault, "2026-09-29", 1200)
    engine, _ = _engine(fast_vault, Path(fast_vault.path.parent))
    total = engine.compute("expense.total", date(2026, 9, 28), date(2026, 10, 4))
    assert total["value"] == pytest.approx(40.0)  # (2800+1200)/100
    assert total["unit"] == "元"


def test_metric_foreign_currency_without_fx_dropped(fast_vault):
    _expense(fast_vault, "2026-09-28", 2800)
    # 非本位币且没有汇率：不进聚合（第 8.9 节）
    _expense(fast_vault, "2026-09-29", 5000, currency="JPY")
    engine, _ = _engine(fast_vault, Path(fast_vault.path.parent))
    total = engine.compute("expense.total", date(2026, 9, 28), date(2026, 10, 4))
    assert total["value"] == pytest.approx(28.0)
    assert total["n"] == 1


def test_metric_completeness_formula(fast_vault):
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        _daily(fast_vault, day, 4)
    engine, _ = _engine(fast_vault, Path(fast_vault.path.parent))
    result = engine.compute("daily.record_completeness", date(2026, 9, 28), date(2026, 10, 4))
    # 7 天里只有 3 天有记录
    assert result["value"] == pytest.approx(3 / 7)
    assert result["lineage"]["formula_id"] == "completeness"


def test_metric_exclusion_double_value(fast_vault):
    # 被意外标记的那天心情很差；其余四天都是 5。min_n=4，排除后仍有 4 条。
    flagged = _daily(fast_vault, "2026-09-28", 1)
    for day in ("2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"):
        _daily(fast_vault, day, 5)
    # 给其中一天挂上会影响 daily.mood 的意外（health 默认排除 daily.mood）
    fast_vault.service.entries.add_anomaly({"type": "health", "impact": 4}, flagged["id"])

    engine, _ = _engine(fast_vault, Path(fast_vault.path.parent))
    result = engine.compute("daily.mood.mean", date(2026, 9, 28), date(2026, 10, 4))
    assert result["value"] == pytest.approx(5.0)  # 排除掉 1 之后
    assert result["value_without_exclusion"] == pytest.approx(21 / 5)
    assert result["excluded_anomalies"] == 1
    assert result["n"] == 4
    assert result["lineage"]["excluded"][0]["entry_id"] == flagged["id"]


def test_metric_plan_completion_rate_never_excludes(fast_vault):
    plan_id = _make_plan(fast_vault.conn, new_ulid())
    planned = _plan_record(
        fast_vault.conn, plan_id, "2026-09-28", planned=10, actual=10, output=10, outcome="done"
    )
    _plan_record(
        fast_vault.conn, plan_id, "2026-09-29", planned=10, actual=0, output=0, outcome="skipped"
    )
    _flag_anomaly(fast_vault, planned)
    engine, _ = _engine(fast_vault, Path(fast_vault.path.parent))
    result = engine.compute("plan.completion_rate", date(2026, 9, 28), date(2026, 10, 4))
    # 完成率永远保留全部记录：10/20 = 0.5，不会因意外被排除
    assert result["value"] == pytest.approx(0.5)
    assert result["excluded_anomalies"] == 0


# ---------------------------------------------------------------------------
# 周期解析
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind,end,start,expected",
    [
        ("weekly", "2026-10-04", None, ("2026-09-28", "2026-10-04")),
        ("monthly", "2026-10-15", None, ("2026-10-01", "2026-10-31")),
        ("yearly", "2026-10-15", None, ("2026-01-01", "2026-12-31")),
        ("custom", "2026-10-04", "2026-09-01", ("2026-09-01", "2026-10-04")),
    ],
)
def test_resolve_period(kind, end, start, expected):
    got = P.resolve_period(kind, end=end, start=start)
    assert (got[0].isoformat(), got[1].isoformat()) == expected


def test_previous_period_is_equal_length():
    start, end = P.resolve_period("weekly", end="2026-10-04")
    prev_start, prev_end = P.previous_period(start, end)
    assert P.period_days(prev_start, prev_end) == P.period_days(start, end)
    assert prev_end == start - timedelta(days=1)


# ---------------------------------------------------------------------------
# 诊断器：8 条规则与边界（参数化）
# ---------------------------------------------------------------------------
def _four_week_rows(output_by_week: list[float], **fixed) -> list[dict]:
    rows = []
    for week, monday in enumerate(WEEK_MONDAYS):
        for offset in (0, 3):
            day = (date.fromisoformat(monday) + timedelta(days=offset)).isoformat()
            rows.append({"day": day, "output": output_by_week[week], **fixed})
    return rows


def _build_case(vault: FastVault, case: str) -> tuple[str, str]:
    """构造一个计划并返回 (plan_id, 期望 verdict)。"""
    conn = vault.conn
    plan_id = new_ulid()
    if case == "insufficient_records":
        _make_plan(conn, plan_id)
        for day in ("2026-09-07", "2026-09-10", "2026-09-14", "2026-09-21", "2026-09-28"):
            _plan_record(conn, plan_id, day, planned=10, actual=10, output=5, outcome="done")
        return plan_id, "insufficient_data"

    if case == "insufficient_span":
        _make_plan(conn, plan_id)
        days = ("2026-09-21", "2026-09-22", "2026-09-23",
                "2026-09-28", "2026-09-29", "2026-09-30")
        for day in days:
            _plan_record(conn, plan_id, day, planned=10, actual=10, output=5, outcome="done")
        return plan_id, "insufficient_data"

    if case == "goal_review_dropped":
        goal_id = _make_goal(conn, new_ulid(), status="dropped")
        _make_plan(conn, plan_id, goal_id=goal_id)
        for row in _four_week_rows([1, 3, 5, 7], planned=10, actual=10, outcome="done"):
            _plan_record(conn, plan_id, row["day"], planned=10, actual=10, output=row["output"],
                         outcome="done")
        return plan_id, "goal_review"

    if case == "goal_review_decision":
        goal_id = _make_goal(conn, new_ulid())
        _make_plan(conn, plan_id, goal_id=goal_id)
        for row in _four_week_rows([1, 3, 5, 7], planned=10, actual=10, outcome="done"):
            _plan_record(conn, plan_id, row["day"], planned=10, actual=10, output=row["output"],
                         outcome="done")
        vault.service.add_goal_checkpoint(goal_id, {"decision": "pause", "progress_note": "先停"})
        return plan_id, "goal_review"

    if case == "keep":
        _make_plan(conn, plan_id)
        for row in _four_week_rows([1, 3, 5, 7], planned=10, actual=10, outcome="done"):
            _plan_record(conn, plan_id, row["day"], planned=10, actual=10, output=row["output"],
                         outcome="done")
        return plan_id, "keep"

    if case == "redesign_flat":
        _make_plan(conn, plan_id)
        for row in _four_week_rows([5, 5, 5, 5], planned=10, actual=10, outcome="done"):
            _plan_record(conn, plan_id, row["day"], planned=10, actual=10, output=row["output"],
                         outcome="done")
        return plan_id, "redesign"

    if case == "retire":
        _make_plan(conn, plan_id)  # 无关联目标 → 关联弱
        monday = date(2026, 8, 3)
        for week in range(9):
            day = (monday + timedelta(days=7 * week)).isoformat()
            _plan_record(conn, plan_id, day, planned=10, actual=0, output=0.0, outcome="skipped")
        return plan_id, "retire"

    if case == "catchup":
        _make_plan(conn, plan_id)
        for index, row in enumerate(
            _four_week_rows([0, 0, 0, 0], planned=10, actual=0, outcome="partial")
        ):
            entry_id = _plan_record(conn, plan_id, row["day"], planned=10, actual=0, output=0.0,
                                    outcome="partial")
            if index % 2 == 0:  # 一半记录带意外 → share = 0.5
                _flag_anomaly(vault, entry_id)
        return plan_id, "catchup"

    if case == "redesign_no_anomaly":
        _make_plan(conn, plan_id)
        for row in _four_week_rows([0, 0, 0, 0], planned=10, actual=0, outcome="skipped"):
            _plan_record(conn, plan_id, row["day"], planned=10, actual=0, output=0.0,
                         outcome="skipped")
        return plan_id, "redesign"

    if case == "watch":
        _make_plan(conn, plan_id)
        for index, row in enumerate(
            _four_week_rows([0, 0, 0, 0], planned=10, actual=0, outcome="partial")
        ):
            entry_id = _plan_record(conn, plan_id, row["day"], planned=10, actual=0, output=0.0,
                                    outcome="partial")
            if index < 2:  # share = 0.25：够不上 catchup，又不是 0 → watch
                _flag_anomaly(vault, entry_id)
        return plan_id, "watch"

    raise AssertionError(f"未知用例：{case}")


DIAGNOSIS_CASES = (
    "insufficient_records",
    "insufficient_span",
    "goal_review_dropped",
    "goal_review_decision",
    "keep",
    "redesign_flat",
    "retire",
    "catchup",
    "redesign_no_anomaly",
    "watch",
)


@pytest.mark.parametrize("case", DIAGNOSIS_CASES)
def test_diagnosis_rules(fast_vault, tmp_path, case):
    config = AppConfig(data_dir=tmp_path / "lifeos")
    diag_cfg = load_diagnosis_config(config.diagnosis_config)
    plan_id, expected = _build_case(fast_vault, case)
    plan = dict(fast_vault.conn.query_one("SELECT * FROM plan WHERE id = ?", (plan_id,)))
    result = diagnose_plan(fast_vault.conn, plan, diag_cfg, as_of=AS_OF)
    assert result["verdict"] == expected, result
    assert result["thresholds_ref"] == diag_cfg.version
    assert result["thresholds"]["completion_low"] == diag_cfg.completion_low


def test_diagnosis_reject_suppresses(fast_vault, tmp_path):
    config = AppConfig(data_dir=tmp_path / "lifeos")
    diag_cfg = load_diagnosis_config(config.diagnosis_config)
    plan_id, _ = _build_case(fast_vault, "keep")
    now = now_utc_iso()
    fast_vault.conn.execute(
        "INSERT INTO user_correction (id, target_type, target_id, kind, created_at)"
        " VALUES (?, 'diagnosis', ?, 'verdict_reject', ?)",
        (new_ulid(), plan_id, now),
    )
    plan = dict(fast_vault.conn.query_one("SELECT * FROM plan WHERE id = ?", (plan_id,)))
    result = diagnose_plan(fast_vault.conn, plan, diag_cfg, as_of=AS_OF)
    assert result["user_rejected"] is True
    assert result["suppressed_until"] is not None


def test_diagnosis_retire_sunk_cost_question(fast_vault, tmp_path):
    config = AppConfig(data_dir=tmp_path / "lifeos")
    diag_cfg = load_diagnosis_config(config.diagnosis_config)
    plan_id, _ = _build_case(fast_vault, "retire")
    plan = dict(fast_vault.conn.query_one("SELECT * FROM plan WHERE id = ?", (plan_id,)))
    result = diagnose_plan(fast_vault.conn, plan, diag_cfg, as_of=AS_OF)
    assert result["verdict"] == "retire"
    assert result["sunk_cost_question"]


# ---------------------------------------------------------------------------
# 相关性
# ---------------------------------------------------------------------------
def _seed_correlated(vault: FastVault, days: list[date], rng: random.Random) -> None:
    plan_id = _make_plan(vault.conn, new_ulid())
    raw = [6.0 + rng.random() * 3.0 for _ in days]
    outcome = [(value - 6.0) / 3.0 for value in raw]
    # 交换前两天的结果：让 rho 略小于 1，避免命中「|rho| >= 1 → p=1」的边界
    outcome[0], outcome[1] = outcome[1], outcome[0]
    for index, day in enumerate(days):
        _health(vault, day.isoformat(), sleep=round(raw[index], 4))
        _entry(
            vault,
            "plan",
            day.isoformat(),
            {"plan_id": plan_id, "planned_units": 1.0, "actual_units": round(outcome[index], 6),
             "outcome": "done"},
        )


def _seed_independent(vault: FastVault, days: list[date], seed: int = 1) -> None:
    """预测与结果两条独立噪声序列（固定种子，最大 |rho| 远低于门槛）。"""
    predictor_rng = random.Random(seed)
    outcome_rng = random.Random(seed + 100000)
    plan_id = _make_plan(vault.conn, new_ulid())
    for day in days:
        sleep = 6.0 + predictor_rng.random() * 3.0
        completion = outcome_rng.random()
        _health(vault, day.isoformat(), sleep=round(sleep, 4))
        _entry(
            vault,
            "plan",
            day.isoformat(),
            {"plan_id": plan_id, "planned_units": 1.0, "actual_units": round(completion, 6),
             "outcome": "done"},
        )


def _corr_setup(fast_vault: FastVault, tmp_path: Path):
    config = AppConfig(data_dir=tmp_path / "lifeos")
    corr_cfg = load_correlation_config(Path(config.config_dir) / "correlation.yaml")
    engine, _ = _engine(fast_vault, tmp_path)
    return corr_cfg, engine


def test_correlation_detects_planted_signal(fast_vault, tmp_path):
    rng = random.Random(20260101)
    days = [date(2026, 8, 26) + timedelta(days=i) for i in range(40)]
    _seed_correlated(fast_vault, days, rng)
    corr_cfg, engine = _corr_setup(fast_vault, tmp_path)
    result = analyze(fast_vault.conn, engine, corr_cfg, days[0], days[-1])
    assert len(result["insights"]) == 1
    insight = result["insights"][0]
    assert insight["pair"] == ["health.sleep.mean_hours", "plan.completion_rate"]
    assert insight["causal_claim"] is False
    assert insight["level"] == "signal"
    assert abs(insight["rho"]) >= corr_cfg.effect_size_min


def test_correlation_is_deterministic(fast_vault, tmp_path):
    rng = random.Random(7)
    days = [date(2026, 8, 26) + timedelta(days=i) for i in range(40)]
    _seed_correlated(fast_vault, days, rng)
    corr_cfg, engine = _corr_setup(fast_vault, tmp_path)
    first = analyze(fast_vault.conn, engine, corr_cfg, days[0], days[-1])
    second = analyze(fast_vault.conn, engine, corr_cfg, days[0], days[-1])
    assert first == second


def test_correlation_noise_yields_no_insight(fast_vault, tmp_path):
    days = [date(2026, 8, 26) + timedelta(days=i) for i in range(40)]
    _seed_independent(fast_vault, days)
    corr_cfg, engine = _corr_setup(fast_vault, tmp_path)
    result = analyze(fast_vault.conn, engine, corr_cfg, days[0], days[-1])
    assert result["insights"] == []


def test_correlation_low_coverage_records_gap(fast_vault, tmp_path):
    # 只在 40 天里的一半有预测指标 → 覆盖率 50% < 70%
    days = [date(2026, 8, 26) + timedelta(days=i) for i in range(40)]
    plan_id = _make_plan(fast_vault.conn, new_ulid())
    for index, day in enumerate(days):
        _entry(
            fast_vault,
            "plan",
            day.isoformat(),
            {"plan_id": plan_id, "planned_units": 1.0, "actual_units": 0.5, "outcome": "done"},
        )
        if index % 2 == 0:
            _health(fast_vault, day.isoformat(), sleep=7.0)
    corr_cfg, engine = _corr_setup(fast_vault, tmp_path)
    result = analyze(fast_vault.conn, engine, corr_cfg, days[0], days[-1])
    assert result["insights"] == []
    assert any(gap["key"] == "corr.gap.coverage" for gap in result["gaps"])


# ---------------------------------------------------------------------------
# 事实包幂等
# ---------------------------------------------------------------------------
def test_facts_is_idempotent(fast_vault, tmp_path):
    for day, mood in (("2026-09-28", 3), ("2026-09-29", 4), ("2026-10-01", 2)):
        _daily(fast_vault, day, mood)
    config = AppConfig(data_dir=tmp_path / "lifeos")
    service = AnalyticsService(fast_vault.conn, config)
    first = service.facts(kind="weekly", end="2026-10-04")
    second = service.facts(kind="weekly", end="2026-10-04")
    assert first == second
    assert first["facts_version"] == 4
    assert set(first) >= {
        "period",
        "profile",
        "coverage",
        "metrics",
        "domain_facts",
        "plans",
        "insights",
        "anomalies",
        "data_gaps",
    }
    assert set(first["domain_facts"]) == {
        "relation",
        "education",
        "career",
        "hobby",
        "idea",
        "dream",
        "resource",
        "income",
        "goal",
    }
    assert len(first["metrics"]) == len(service.registry.keys())
    # 未记录领域应出现在 data_gaps
    assert any(
        gap["key"] == "facts.gap.domain_empty" and gap["params"]["domain"] == "education"
        for gap in first["data_gaps"]
    )


def test_entry_texts_keep_final_entries_with_any_content(fast_vault):
    """内容层只收正式、未删除的条目；标题/备注/标签/详情任一非空就保留。

    v4 起不再要求标题或备注非空：只填了数值的条目同样要参与分析，
    否则「设了字段却没进报告」的缺口仍然存在。人物姓名在本地换成代号。
    """
    now = now_utc_iso()
    fast_vault.conn.execute(
        "INSERT INTO person (id, code, display_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (new_ulid(), "P01", "张三", now, now),
    )
    kept = fast_vault.service.entries.create(
        {
            "domain": "daily",
            "occurred_at": "2026-09-28T09:05:00+08:00",
            "title": "和 张三 复盘",
            "note": "聊了下一步",
            "detail": {"mood": 4},
        }
    )
    fast_vault.service.entries.create(
        {
            "domain": "daily",
            "occurred_at": "2026-09-29T09:05:00+08:00",
            "title": "草稿标题",
            "note": "草稿备注",
            "status": "draft",
        }
    )
    deleted = fast_vault.service.entries.create(
        {
            "domain": "daily",
            "occurred_at": "2026-09-30T09:05:00+08:00",
            "title": "已删除的标题",
            "note": "已删除的备注",
            "detail": {"mood": 3},
        }
    )
    fast_vault.service.entries.delete(deleted["id"])
    # 正式但标题与备注都为空：只要详情非空就仍然保留（v4 口径）
    _daily(fast_vault, "2026-10-01", 3)

    block = build_entry_texts(fast_vault.conn, date(2026, 9, 28), date(2026, 10, 4))
    # v4 起标题/备注/标签/详情任一非空即保留：「只填了心情」的条目也要参与分析
    assert block["total"] == 2
    assert block["included"] == 2
    assert [item["title"] for item in block["items"]] == ["和 person_P01 复盘", ""]
    assert block["items"][0]["note"] == "聊了下一步"
    assert block["items"][0]["tags"] == []
    assert block["items"][0]["details"]  # 详情字段（心情）也带出来
    assert block["items"][1]["details"]  # 只有详情、没有标题备注的条目同样保留
    assert kept["id"]


def test_entry_texts_respect_domain_scope(fast_vault):
    """领域过滤与 guardian 裁剪共用同一批 domain，别的领域条目不会混进来。"""
    fast_vault.service.entries.create(
        {
            "domain": "health",
            "occurred_at": "2026-09-28T08:00:00+08:00",
            "note": "睡得一般",
            "detail": {"sleep_hours": 6.5},
        }
    )
    block = build_entry_texts(
        fast_vault.conn,
        date(2026, 9, 28),
        date(2026, 10, 4),
        domains=frozenset({"health"}),
    )
    assert block["total"] == 1
    assert block["items"][0]["domain"] == "health"
    empty = build_entry_texts(
        fast_vault.conn,
        date(2026, 9, 28),
        date(2026, 10, 4),
        domains=frozenset({"daily"}),
    )
    assert empty == {"total": 0, "included": 0, "items": []}


def test_monthly_and_yearly_facts_carry_history(fast_vault, tmp_path):
    """跨月对比：月报带近 6 个月、年报带整年 12 个月的逐月数字（第 9.1 节）。"""
    for month in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"):
        _daily(fast_vault, f"{month}-01", 4)
    service = AnalyticsService(fast_vault.conn, AppConfig(data_dir=tmp_path / "lifeos"))
    assert "history" not in service.facts(kind="weekly", end="2026-10-04")
    history = service.facts(kind="monthly", end="2026-10-04")["history"]
    assert [row["month"] for row in history] == [
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    assert set(history[0]) == {
        "month",
        "record_completeness",
        "expense_total",
        "mood_mean",
        "exercise_minutes",
        "relation_entries",
        "career_minutes_total",
        "education_minutes_total",
        "hobby_minutes_total",
    }
    # 收入一律不进跨月历史（第 7.4 节）
    assert all("income" not in key for row in history for key in row)
    yearly = service.facts(kind="yearly", end="2026-10-04")["history"]
    assert len(yearly) == 12
    assert yearly[0]["month"] == "2026-01"
    assert yearly[-1]["month"] == "2026-12"


def test_service_metrics_and_explain(fast_vault, tmp_path):
    _daily(fast_vault, "2026-09-28", 4)
    config = AppConfig(data_dir=tmp_path / "lifeos")
    service = AnalyticsService(fast_vault.conn, config)
    block = service.metrics(kind="weekly", end="2026-10-04", keys=["daily.mood.mean"])
    assert block["period"]["start"] == "2026-09-28"
    assert [item["key"] for item in block["items"]] == ["daily.mood.mean"]
    explained = service.explain(kind="weekly", end="2026-10-04", metric="daily.mood.mean")
    assert explained["target"] == {"type": "metric", "id": "daily.mood.mean"}
    assert "lineage" in explained


# ---------------------------------------------------------------------------
# 统计原语
# ---------------------------------------------------------------------------
def test_stats_primitives():
    assert stats.mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    std = stats.sample_std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert std == pytest.approx(2.13809, rel=1e-4)
    assert stats.spearman([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)
    slope = stats.linear_slope([0.0, 1.0, 2.0, 3.0], [1.0, 3.0, 5.0, 7.0])
    assert slope is not None and slope["slope"] == pytest.approx(2.0)
    # BH：最小 p 显著则被拒绝
    passed = stats.benjamini_hochberg([0.001, 0.5, 0.6], 0.1)
    assert passed == [True, False, False]


# ---------------------------------------------------------------------------
# 分析端点契约
# ---------------------------------------------------------------------------
TOKEN = "analytics-token-0123456789"
BASE_URL = "http://127.0.0.1:8000"
PROFILE_ID = "01J0PROFILETEST000000000000"


@pytest.fixture
def vault(tmp_path):
    built = build_vault(tmp_path)
    yield built
    built.conn.close()


@pytest.fixture
def app(data_dir, vault, monkeypatch):
    config = AppConfig(data_dir=data_dir)
    application = create_app(config, token=TOKEN)
    state = application.state.lifeos
    state.profiles.registry.add(
        ProfileRecord(
            id=PROFILE_ID,
            alias="测试档案",
            vault_path=str(vault.path.parent),
            kdf_params={},
            wrapped_keys=[],
            created_at=now_utc_iso(),
        )
    )
    unlocked = UnlockedProfile(
        profile_id=PROFILE_ID,
        alias="测试档案",
        dek=vault.dek,
        meta={"age_band": "adult", "egress_policy": "none"},
    )
    monkeypatch.setattr(state.profiles, "vault_path", lambda _pid: vault.path)
    monkeypatch.setattr(state.profiles, "blobs_dir", lambda _pid: vault.blobs_dir)
    monkeypatch.setattr(state.profiles, "unlock", lambda _pid, _pw: unlocked)
    monkeypatch.setattr(state.profiles, "unlock_with_recovery", lambda _pid, _key: unlocked)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE_URL) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield test_client


def _unlock(client):
    response = client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"})
    assert response.status_code == 200, response.text


def test_analytics_endpoints_locked(client):
    for path in ("/metrics", "/facts", "/explain"):
        assert client.get(f"{API_PREFIX}{path}").status_code == 423


def test_metrics_endpoint(client):
    _unlock(client)
    response = client.get(f"{API_PREFIX}/metrics", params={"period": "weekly", "end": "2026-10-04"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["period"]["end"] == "2026-10-04"
    assert len(body["items"]) >= 10


def test_facts_endpoint(client):
    _unlock(client)
    response = client.get(f"{API_PREFIX}/facts", params={"period": "weekly", "end": "2026-10-04"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["facts_version"] == 4
    assert "coverage" in body and "plans" in body
    assert "domain_facts" in body


def test_explain_endpoint(client):
    _unlock(client)
    response = client.get(
        f"{API_PREFIX}/explain",
        params={"period": "weekly", "end": "2026-10-04", "metric": "daily.mood.mean"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target"]["type"] == "metric"
    assert "lineage" in body


def test_explain_requires_a_target(client):
    _unlock(client)
    response = client.get(f"{API_PREFIX}/explain", params={"period": "weekly", "end": "2026-10-04"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_plan_diagnosis_unknown_plan(client):
    _unlock(client)
    response = client.get(f"{API_PREFIX}/plans/nope/diagnosis")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
