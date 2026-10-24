"""事实包（第 8.7 节）：本地确定性代码算出的统计结果，LLM 只读它。

结构固定为 facts_version / period / profile / coverage / metrics /
entry_texts / domain_facts / plans / insights / anomalies / data_gaps。每个数字都能追溯到
来源条目或公式（8.10）。同样的数据和配置，永远得到同样的 JSON。

entry_texts 是报告的「内容层」：本期条目的标题、备注与时间戳，只随报告出站，
不参与指标计算；人物姓名在本地先换成代号，敏感标识仍由隐私网关兜底扫描。

domain_facts 是 7 个没有指标的定性领域的「过程统计」（次数、时长、状态分布、
转化数），只统计记录与流转，不评价内容质量——想法与梦境尤其不做内容打分。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from ..db.connection import VaultConnection
from ..domain import schema_defs as sd
from ..domain.errors import InvalidInputError
from ..domain.guardian import GuardianPolicy
from ..i18n import DEFAULT_LOCALE, message, t
from . import periods as P
from .correlation import CorrelationConfig, analyze
from .diagnosis import DiagnosisConfig, diagnose_plan
from .domain_facts import DOMAIN_FACTS_VERSION, build_domain_facts
from .metrics import MetricEngine
from .registry import MetricRegistry

# v4：entry_texts 每条带上 tags 与 details（该记录的全部详情字段），
# 并新增 goals 区块（目标的标题、目标值、why_note）。此前详情列只入库、
# 不进事实包，等于「设计了却没参与分析」。
FACTS_VERSION = 4
_CODE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 条目标题/备注出站的裁剪口径：只用来给模型提供上下文，不做全文归档。
_MAX_ENTRY_TITLE_CHARS = 120
_MAX_ENTRY_NOTE_CHARS = 500
_MAX_DETAIL_TEXT_CHARS = 400

# 详情列 → (字段标签 i18n 键, 值形态)。值形态：
# text 原样（截断）；json 解析成列表；money 从最小单位换算成票面金额；
# option:<组> 用 option.<组>.<值> 本地化；ref:<对象> 换成对象名称；
# direction 收/支方向。未登记的列不会出站。
_DETAIL_SPECS: dict[str, dict[str, tuple[str, str]]] = {
    "daily_log": {
        "mood": ("field.mood", "text"),
        "energy": ("field.energy", "text"),
        "stress": ("field.stress", "text"),
        "day_rating": ("field.day_rating", "text"),
        "one_line": ("field.one_line", "text"),
    },
    "txn": {
        "direction": ("field.direction", "direction"),
        "amount_minor": ("field.amount", "money"),
        "currency": ("field.currency", "text"),
        "category": ("field.category", "option:expense_category"),
        "essential": ("field.essential", "text"),
        "recurring": ("field.recurring", "text"),
        "budgeted": ("field.budgeted", "text"),
        "person_id": ("field.person", "ref:person"),
    },
    "resource_record": {
        "resource_id": ("field.resource", "ref:resource"),
        "kind": ("field.kind", "option:resource_kind"),
        "name": ("field.name", "text"),
        "value": ("field.value", "text"),
        "unit": ("field.unit", "text"),
        "delta": ("field.delta", "text"),
        "reason": ("field.reason", "text"),
    },
    "goal_checkpoint": {
        "goal_id": ("field.goal", "ref:goal"),
        "metric_value": ("field.metric_value", "text"),
        "confidence": ("field.confidence", "text"),
        "decision": ("field.decision", "option:goal_decision"),
        "progress_note": ("field.progress_note", "text"),
    },
    "relation_record": {
        "person_id": ("field.person", "ref:person"),
        "kind": ("field.kind", "option:relation_kind"),
        "quality": ("field.quality", "text"),
        "reciprocity": ("field.reciprocity", "text"),
        "initiated_by": ("field.initiated_by", "option:initiated_by"),
    },
    "health_log": {
        "sleep_hours": ("field.sleep_hours", "text"),
        "sleep_quality": ("field.sleep_quality", "text"),
        "height_cm": ("field.height_cm", "text"),
        "weight_kg": ("field.weight_kg", "text"),
        "exercise_minutes": ("field.exercise_minutes", "text"),
        "exercise_type": ("field.exercise_type", "text"),
        "diet_quality": ("field.diet_quality", "text"),
        "symptoms_json": ("field.symptoms", "json"),
        "measures_json": ("field.measures", "json"),
        "meds_note": ("field.meds_note", "text"),
    },
    "education_record": {
        "subject": ("field.subject", "text"),
        "activity": ("field.activity", "option:education_activity"),
        "minutes": ("field.minutes", "text"),
        "score": ("field.score", "text"),
        "score_max": ("field.score_max", "text"),
        "skill_id": ("field.skill", "ref:skill"),
    },
    "career_record": {
        "activity": ("field.activity", "option:career_activity"),
        "minutes": ("field.minutes", "text"),
        "output_desc": ("field.output_desc", "text"),
        "impact": ("field.impact", "text"),
        "opportunity": ("field.opportunity", "text"),
        "skill_id": ("field.skill", "ref:skill"),
    },
    "hobby_record": {
        "hobby_id": ("field.hobby", "ref:hobby"),
        "minutes": ("field.minutes", "text"),
        "satisfaction": ("field.satisfaction", "text"),
        "progress_note": ("field.progress_note", "text"),
    },
    "plan_record": {
        "plan_id": ("field.plan", "ref:plan"),
        "planned_units": ("field.planned_units", "text"),
        "actual_units": ("field.actual_units", "text"),
        "unit": ("field.unit", "text"),
        "output_value": ("field.output_value", "text"),
        "outcome": ("field.outcome", "option:plan_outcome"),
    },
    "idea_record": {
        "status": ("field.state", "option:idea_status"),
        "spawned_plan_id": ("field.spawned_plan", "ref:plan"),
        "spawned_goal_id": ("field.spawned_goal", "ref:goal"),
    },
    "dream_record": {
        "clarity": ("field.clarity", "text"),
        "emotion": ("field.emotion", "text"),
        "themes_json": ("field.themes", "json"),
        "recurring": ("field.dream_recurring", "text"),
    },
}

# 长期对象 id → 名称：人物用代号，目标/计划/资源/爱好/技能用名称或标题
_REF_QUERIES: dict[str, tuple[str, str, bool]] = {
    "goal": ("goal", "title", True),
    "plan": ("plan", "title", True),
    "resource": ("resource_account", "name", False),
    "hobby": ("hobby", "name", False),
    "skill": ("skill", "name", False),
}

# 跨月对比的窗口：月报看近 6 个月，年报看整年 12 个月（只对这两种报告出 history）。
HISTORY_MONTHS = {"monthly": 6, "yearly": 12}
_HISTORY_DOMAIN_FIELDS = {
    "relation": ("entries",),
    "career": ("minutes_total",),
    "education": ("minutes_total",),
    "hobby": ("minutes_total",),
}
# history 里由指标算出的字段属于哪个领域：guardian 会话下不可见就连这一列都不算
_HISTORY_METRIC_KEYS = {
    "record_completeness": "daily.record_completeness",
    "expense_total": "expense.total",
    "mood_mean": "daily.mood.mean",
    "exercise_minutes": "health.exercise.minutes_total",
}
_HISTORY_METRIC_DOMAINS = {
    "record_completeness": "daily",
    "expense_total": "expense",
    "mood_mean": "daily",
    "exercise_minutes": "health",
}
# 反向映射：history 行里的每个字段 → 所属领域（guardian 再读时逐列裁剪）
_HISTORY_KEY_DOMAINS = {
    **_HISTORY_METRIC_DOMAINS,
    **{
        f"{domain}_{field}": domain
        for domain, fields in _HISTORY_DOMAIN_FIELDS.items()
        for field in fields
    },
}


def _round(value: float | None, nd: int = 4) -> float | None:
    return None if value is None else round(value, nd)


def build_coverage(
    conn: VaultConnection, start: date, end: date, domains: frozenset[str] | None = None
) -> dict[str, str]:
    days = P.period_days(start, end)
    rows = conn.query(
        "SELECT domain, COUNT(DISTINCT occurred_date) AS c FROM entry"
        " WHERE deleted_at IS NULL AND status = 'final'"
        " AND occurred_date >= ? AND occurred_date <= ? GROUP BY domain",
        (start.isoformat(), end.isoformat()),
    )
    counts = {r["domain"]: int(r["c"]) for r in rows}
    source = sd.ALL_DOMAINS if domains is None else [d for d in sd.ALL_DOMAINS if d in domains]
    return {domain: f"{counts.get(domain, 0)}/{days}" for domain in source}


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _person_code_map(conn: VaultConnection) -> list[tuple[str, str]]:
    """本地人物姓名 → 代号；出站前先把姓名替换成代号，保留内容又不带姓名。"""
    rows = conn.query(
        "SELECT display_name, code FROM person"
        " WHERE deleted_at IS NULL AND display_name IS NOT NULL"
    )
    pairs = [
        (str(row["display_name"]).strip(), str(row["code"]).strip())
        for row in rows
        if str(row["display_name"]).strip() and str(row["code"]).strip()
    ]
    # 长名字先换，避免「小王」先于「小王老师」被替换
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


def _json_items(raw: Any) -> list[str]:
    """JSON 数组列（tags / themes / symptoms / measures）解析成字符串列表。"""
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _ref_names(conn: VaultConnection) -> dict[str, dict[str, str]]:
    """长期对象 id → 可读名称。人物只给代号，其余给标题/名称。"""
    names: dict[str, dict[str, str]] = {
        "person": {
            str(row["id"]): f"person_{row['code']}"
            for row in conn.query(
                "SELECT id, code FROM person WHERE deleted_at IS NULL"
            )
        }
    }
    for kind, (table, column, soft_delete) in _REF_QUERIES.items():
        where = " WHERE deleted_at IS NULL" if soft_delete else ""
        names[kind] = {
            str(row["id"]): str(row[column])
            for row in conn.query(f"SELECT id, {column} FROM {table}{where}")
        }
    return names


def _detail_value(
    kind: str,
    raw: Any,
    row: dict[str, Any],
    names: dict[str, dict[str, str]],
    locale: str,
) -> Any:
    if raw is None or raw == "":
        return None
    if kind == "json":
        items = _json_items(raw)
        return items or None
    if kind == "money":
        minor_exp = row.get("minor_exp")
        factor = 10 ** (int(minor_exp) if minor_exp is not None else 2)
        return round(float(raw) / factor, 2)
    if kind.startswith("ref:"):
        return names.get(kind[4:], {}).get(str(raw), str(raw))
    if kind.startswith("option:"):
        group = kind[7:]
        key = f"option.{group}.{raw}"
        label = t(key, locale)
        return raw if label == key else label
    if kind == "direction":
        key = f"option.direction.{raw}"
        label = t(key, locale)
        return raw if label == key else label
    text = str(raw).strip()
    return text if len(text) <= _MAX_DETAIL_TEXT_CHARS else text[:_MAX_DETAIL_TEXT_CHARS] + "…"


def _entry_details(
    conn: VaultConnection,
    ids_by_domain: dict[str, list[str]],
    names: dict[str, dict[str, str]],
    locale: str,
) -> dict[str, dict[str, Any]]:
    """把每条记录的全部详情列摊平成「字段标签: 值」，供报告模型理解内容。

    这是 v4 的关键补口：此前 daily/health/expense 等详情列只入库，
    没有任何 SELECT 读它们，所以设了字段也不参与分析。
    """
    out: dict[str, dict[str, Any]] = {}
    for domain, ids in ids_by_domain.items():
        table = sd.DOMAIN_DETAIL_TABLE[domain]
        spec = _DETAIL_SPECS.get(table)
        if not spec or not ids:
            continue
        columns = [column for column in sd.DETAIL_COLUMNS[table] if column in spec]
        if not columns:
            continue
        placeholders = ",".join("?" for _ in ids)
        selected = ", ".join(f"t.{column}" for column in columns)
        rows = conn.query(
            f"SELECT t.entry_id AS entry_id, {selected} FROM {table} t"
            f" WHERE t.entry_id IN ({placeholders})",
            tuple(ids),
        )
        for raw_row in rows:
            row = dict(raw_row)
            details: dict[str, Any] = {}
            for column in columns:
                label_key, kind = spec[column]
                value = _detail_value(kind, row.get(column), row, names, locale)
                if value is not None and value != "":
                    details[t(label_key, locale)] = value
            if details:
                out[str(row["entry_id"])] = details
    return out


def build_entry_texts(
    conn: VaultConnection,
    start: date,
    end: date,
    domains: frozenset[str] | None = None,
    locale: str = DEFAULT_LOCALE,
) -> dict[str, Any]:
    """本期条目的标题、备注、标签、时间戳与该记录的全部详情字段。

    只收正式记录；草稿与软删条目不出现在这里。姓名先换成本地人物代号。
    v4 起不再要求标题或备注非空：只填了详情字段的记录同样保留，
    这样「只记了睡眠时长、没写备注」的条目也能参与分析。
    """
    sql = (
        "SELECT id, occurred_at, domain, title, note, tags_json FROM entry"
        " WHERE deleted_at IS NULL AND status = 'final'"
        " AND occurred_date >= ? AND occurred_date <= ?"
    )
    params: list[Any] = [start.isoformat(), end.isoformat()]
    if domains is not None:
        if not domains:
            return {"total": 0, "included": 0, "items": []}
        placeholders = ",".join("?" for _ in domains)
        sql += f" AND domain IN ({placeholders})"
        params.extend(sorted(domains))
    sql += " ORDER BY occurred_at ASC"

    rows = conn.query(sql, params)
    codes = _person_code_map(conn)
    names = _ref_names(conn)
    ids_by_domain: dict[str, list[str]] = {}
    for row in rows:
        ids_by_domain.setdefault(str(row["domain"]), []).append(str(row["id"]))
    details_by_entry = _entry_details(conn, ids_by_domain, names, locale)

    items: list[dict[str, Any]] = []
    for row in rows:
        title = _clip_text(row["title"], _MAX_ENTRY_TITLE_CHARS)
        note = _clip_text(row["note"], _MAX_ENTRY_NOTE_CHARS)
        tags = _json_items(row["tags_json"])
        details = details_by_entry.get(str(row["id"]), {})
        if not title and not note and not tags and not details:
            continue
        for name, code in codes:
            title = title.replace(name, f"person_{code}")
            note = note.replace(name, f"person_{code}")
        items.append(
            {
                # 保留到分钟的时间戳；用 ISO 的 T 分隔，避免被网关误判成连续数字串
                "at": str(row["occurred_at"])[:16].replace(" ", "T"),
                "domain": str(row["domain"]),
                "title": title,
                "note": note,
                "tags": tags,
                "details": details,
            }
        )
    return {"total": len(rows), "included": len(items), "items": items}


def build_goals(
    conn: VaultConnection, domains: frozenset[str] | None = None
) -> list[dict[str, Any]]:
    """长期目标：标题、目标值、why_note 与关联计划数。

    v4 之前 goal 领域只有状态分布，模型不知道用户到底在追什么目标，
    「建议要落在目标上」就无从谈起。
    """
    if domains is not None and "goal" not in domains:
        return []
    rows = conn.query(
        "SELECT * FROM goal WHERE deleted_at IS NULL ORDER BY created_at, id"
    )
    plan_counts = {
        str(row["goal_id"]): int(row["c"])
        for row in conn.query(
            "SELECT goal_id, COUNT(*) AS c FROM plan"
            " WHERE deleted_at IS NULL AND goal_id IS NOT NULL GROUP BY goal_id"
        )
    }
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        letter = _CODE_LETTERS[index] if index < len(_CODE_LETTERS) else f"Z{index}"
        out.append(
            {
                "ref": f"goal_{letter}",
                "title": row["title"],
                "area": row["area"],
                "horizon": row["horizon"],
                "metric_name": row["metric_name"],
                "metric_unit": row["metric_unit"],
                "direction": row["direction"],
                "baseline": _round(row["baseline"]),
                "target": _round(row["target"]),
                "target_date": row["target_date"],
                "status": row["status"],
                "why_note": row["why_note"],
                "plan_count": plan_counts.get(str(row["id"]), 0),
            }
        )
    return out


def build_metrics(
    engine: MetricEngine,
    registry: MetricRegistry,
    start: date,
    end: date,
    *,
    keys: list[str] | None = None,
    domains: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    prev_start, prev_end = P.previous_period(start, end)
    out: list[dict[str, Any]] = []
    for key in keys or registry.keys():
        metric_def = registry.get(key)
        if domains is not None and metric_def.domain not in domains:
            continue  # guardian 会话下只算可见领域的指标（第 7.7 节、验收 A2）
        current = engine.compute(key, start, end)
        prev = engine.compute(key, prev_start, prev_end)
        delta = None
        if current["value"] is not None and prev["value"] is not None:
            delta = _round(current["value"] - prev["value"])
        out.append(
            {
                "key": key,
                "description": metric_def.description,
                "unit": metric_def.unit,
                "value_kind": metric_def.value_kind,
                "value": _round(current["value"]),
                "prev_value": _round(prev["value"]),
                "delta": delta,
                "n": current["n"],
                "status": current["status"],
                "reason": current["reason"],
                "excluded_anomalies": current["excluded_anomalies"],
                "value_without_exclusion": _round(current["value_without_exclusion"]),
                "lineage": current["lineage"],
            }
        )
    return out


def build_plans(
    conn: VaultConnection,
    diag_cfg: DiagnosisConfig,
    as_of: date,
    domains: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    if domains is not None and "plan" not in domains:
        return []
    plans = conn.query(
        "SELECT * FROM plan WHERE deleted_at IS NULL AND status = 'active'"
        " ORDER BY created_at, id"
    )
    out: list[dict[str, Any]] = []
    for i, plan in enumerate(plans):
        plan_dict = dict(plan)
        letter = _CODE_LETTERS[i] if i < len(_CODE_LETTERS) else f"Z{i}"
        plan_dict["ref"] = f"plan_{letter}"
        diagnosis = diagnose_plan(conn, plan_dict, diag_cfg, as_of=as_of)
        out.append(
            {
                "ref": plan_dict["ref"],
                # 标题只在本地渲染时用到（出站白名单不含 title），用来把
                # 报告里的 plan_A 换成用户一眼能懂的计划名。
                "title": plan_dict.get("title"),
                "completion_rate": _round(diagnosis["completion_rate"]),
                "anomaly_share": _round(diagnosis["anomaly_share"]),
                "output_trend": diagnosis["output_trend"],
                "diagnosis": {
                    "verdict": diagnosis["verdict"],
                    "reasons": diagnosis["reasons"],
                    "thresholds_ref": diagnosis["thresholds_ref"],
                    "user_rejected": diagnosis["user_rejected"],
                },
            }
        )
    return out


def build_insights(
    conn: VaultConnection,
    engine: MetricEngine,
    corr_cfg: CorrelationConfig,
    start: date,
    end: date,
    domains: frozenset[str] | None = None,
) -> dict[str, Any]:
    block = analyze(conn, engine, corr_cfg, start, end)
    if domains is not None:
        # 相关性提示是跨领域的推断，guardian 会话下整块不返回（fail-closed）
        return {**block, "insights": [], "gaps": []}
    return block


def _last_day_of_month(day: date) -> date:
    first = day.replace(day=1)
    return (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)


def _metric_number(
    engine: MetricEngine, key: str, start: date, end: date
) -> float | None:
    result = engine.compute(key, start, end)
    if result["status"] != "ok":
        return None
    return _round(result["value"])


def build_history(
    conn: VaultConnection,
    engine: MetricEngine,
    *,
    end: date,
    months: int,
    domains: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """近 N 个自然月的关键数字，供月报/年报做跨月对比（本地确定性，不联网）。

    只取少数几个稳定口径：记录完整度、支出、睡眠情绪相关的均值、运动时长，
    以及关系/事业/学习/兴趣的记录条数与时长。收入一律不进 history。
    """
    out: list[dict[str, Any]] = []
    cursor = end.replace(day=1)
    for _ in range(months):
        first = cursor
        last = _last_day_of_month(first)
        record: dict[str, Any] = {"month": first.strftime("%Y-%m")}
        for field, metric_key in _HISTORY_METRIC_KEYS.items():
            if domains is not None and _HISTORY_METRIC_DOMAINS[field] not in domains:
                continue
            record[field] = _metric_number(engine, metric_key, first, last)
        domain_block = build_domain_facts(conn, first, last, domains=domains)
        for domain, fields in _HISTORY_DOMAIN_FIELDS.items():
            if domains is not None and domain not in domains:
                continue
            block = domain_block.get(domain) or {}
            for field in fields:
                record[f"{domain}_{field}"] = block.get(field)
        out.append(record)
        cursor = (first - timedelta(days=1)).replace(day=1)
    out.reverse()
    return out


def _anomaly_summary(
    conn: VaultConnection, start: date, end: date, domains: frozenset[str] | None = None
) -> dict[str, int]:
    sql = (
        "SELECT COUNT(*) AS c, COALESCE(SUM(a.impact), 0) AS s"
        " FROM anomaly a JOIN entry e ON e.id = a.entry_id"
        " WHERE e.deleted_at IS NULL AND e.occurred_date >= ? AND e.occurred_date <= ?"
    )
    params: list[Any] = [start.isoformat(), end.isoformat()]
    if domains is not None:
        if not domains:
            return {"count": 0, "impact_sum": 0}
        placeholders = ",".join("?" for _ in domains)
        sql += f" AND e.domain IN ({placeholders})"
        params.extend(sorted(domains))
    row = conn.query_one(sql, params)
    if row is None:
        return {"count": 0, "impact_sum": 0}
    return {"count": int(row["c"]), "impact_sum": int(row["s"])}


def build_facts(
    conn: VaultConnection,
    registry: MetricRegistry,
    engine: MetricEngine,
    corr_cfg: CorrelationConfig,
    diag_cfg: DiagnosisConfig,
    *,
    kind: str,
    start: date,
    end: date,
    age_band: str,
    locale: str = DEFAULT_LOCALE,
    domains: frozenset[str] | None = None,
) -> dict[str, Any]:
    metrics = build_metrics(engine, registry, start, end, domains=domains)
    coverage = build_coverage(conn, start, end, domains=domains)
    entry_texts = build_entry_texts(conn, start, end, domains=domains, locale=locale)
    domain_facts = build_domain_facts(conn, start, end, domains=domains)
    goals = build_goals(conn, domains=domains)
    plans = build_plans(conn, diag_cfg, end, domains=domains)
    insight_block = build_insights(conn, engine, corr_cfg, start, end, domains=domains)
    anomalies = _anomaly_summary(conn, start, end, domains=domains)
    history = (
        build_history(
            conn, engine, end=end, months=HISTORY_MONTHS[kind], domains=domains
        )
        if kind in HISTORY_MONTHS
        else None
    )

    gaps: list[dict[str, Any]] = []
    for domain, value in coverage.items():
        if value.startswith("0/"):
            gaps.append(message("facts.gap.domain_empty", domain=domain))
    for metric in metrics:
        if metric["status"] != "ok":
            gaps.append(
                message("facts.gap.metric_not_ok", metric=metric["key"], status=metric["status"])
            )
    gaps.extend(insight_block["gaps"])

    payload: dict[str, Any] = {
        "facts_version": FACTS_VERSION,
        "period": {"kind": kind, "start": start.isoformat(), "end": end.isoformat()},
        "profile": {"age_band": age_band, "locale": locale},
        "coverage": coverage,
        "metrics": metrics,
        # 本期条目标题/备注：报告的「内容层」，只随报告出站，不参与指标计算
        "entry_texts": entry_texts,
        "domain_facts_version": DOMAIN_FACTS_VERSION,
        "domain_facts": domain_facts,
        # 长期目标：标题、目标值、why_note。模型要「把建议落在目标上」就必须看到它
        "goals": goals,
        "plans": plans,
        "insights": insight_block["insights"],
        "insights_version": insight_block["version"],
        "anomalies": anomalies,
        "data_gaps": gaps,
    }
    if history is not None:
        payload["history"] = history
    return payload


def scope_facts(
    facts: dict[str, Any],
    policy: GuardianPolicy,
    registry: MetricRegistry | None = None,
) -> dict[str, Any]:
    """把已算好的事实包按 guardian 可见范围再裁一遍（第 7.7 节、验收 A2）。

    `build_facts` 在建包时就按 domains 裁剪；这里处理的是**先前由 owner 会话生成、
    已落库**的事实包，在 guardian 读取时按当前可见范围复裁（历史报告再读路径）。
    """
    out = dict(facts)
    coverage = facts.get("coverage")
    if isinstance(coverage, dict):
        out["coverage"] = {
            str(d): v for d, v in coverage.items() if policy.can_see_domain(str(d))
        }

    metrics = facts.get("metrics")
    if isinstance(metrics, list):
        kept_metrics: list[Any] = []
        for item in metrics:
            domain = str(item.get("domain") or "")
            if not domain and registry is not None:
                try:
                    domain = registry.get(str(item.get("key"))).domain
                except InvalidInputError:
                    domain = ""
            if not domain or policy.can_see_domain(domain):
                kept_metrics.append(item)
        out["metrics"] = kept_metrics

    domain_facts = facts.get("domain_facts")
    if isinstance(domain_facts, dict):
        out["domain_facts"] = {
            str(domain): value
            for domain, value in domain_facts.items()
            if policy.can_see_domain(str(domain))
        }

    entry_texts = facts.get("entry_texts")
    if isinstance(entry_texts, dict):
        raw_items = entry_texts.get("items")
        items = [
            item
            for item in (raw_items if isinstance(raw_items, list) else [])
            if isinstance(item, dict) and policy.can_see_domain(str(item.get("domain") or ""))
        ]
        out["entry_texts"] = {
            "total": len(items),
            "included": len(items),
            "items": items,
        }

    if not policy.can_see_domain("plan"):
        out["plans"] = []
    if not policy.can_see_domain("goal"):
        out["goals"] = []
    history = facts.get("history")
    if isinstance(history, list):
        kept_history: list[Any] = []
        for row in history:
            if not isinstance(row, dict):
                continue
            kept_history.append(
                {
                    str(key): value
                    for key, value in row.items()
                    if key == "month"
                    or policy.can_see_domain(_HISTORY_KEY_DOMAINS.get(str(key), ""))
                }
            )
        out["history"] = kept_history
    # 相关性提示是跨领域推断，guardian 会话下整块不返回
    out["insights"] = []

    gaps = facts.get("data_gaps")
    if isinstance(gaps, list):
        keep_keys = {str(m.get("key")) for m in out.get("metrics", [])}
        allowed_domains = set(policy.visible_domains)
        scoped: list[Any] = []
        for gap in gaps:
            params = gap.get("params") if isinstance(gap, dict) else None
            if isinstance(params, dict):
                if "domain" in params and str(params["domain"]) not in allowed_domains:
                    continue
                if "metric" in params and str(params["metric"]) not in keep_keys:
                    continue
            scoped.append(gap)
        out["data_gaps"] = scoped
    return out
