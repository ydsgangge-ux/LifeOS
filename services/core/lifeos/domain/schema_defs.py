"""领域字段规范（第 5 章）：领域到详情表的映射、可写列白名单、
意外的默认排除范围（5.2）、默认敏感度（4.4）。

这些常量被 domain 与 api 共用，避免列名拼接错误与 SQL 注入。
"""

from __future__ import annotations

# 领域 → 详情表（第 5.1 节，13 个领域）
DOMAIN_DETAIL_TABLE: dict[str, str] = {
    "daily": "daily_log",
    "income": "txn",
    "expense": "txn",
    "resource": "resource_record",
    "goal": "goal_checkpoint",
    "relation": "relation_record",
    "health": "health_log",
    "education": "education_record",
    "career": "career_record",
    "hobby": "hobby_record",
    "plan": "plan_record",
    "idea": "idea_record",
    "dream": "dream_record",
}

ALL_DOMAINS: tuple[str, ...] = tuple(DOMAIN_DETAIL_TABLE.keys())

# 每个详情表的可写列（不含 entry_id）。未知字段一律进 entry.extra_json（第 6.4 节）。
DETAIL_COLUMNS: dict[str, tuple[str, ...]] = {
    "daily_log": ("mood", "energy", "stress", "day_rating", "one_line"),
    "txn": (
        "direction",
        "amount_minor",
        "currency",
        "minor_exp",
        "fx_rate_to_base",
        "category",
        "essential",
        "recurring",
        "budgeted",
        "person_id",
    ),
    "resource_record": ("resource_id", "kind", "name", "value", "unit", "delta", "reason"),
    "goal_checkpoint": (
        "goal_id",
        "metric_value",
        "confidence",
        "decision",
        "progress_note",
    ),
    "relation_record": (
        "person_id",
        "kind",
        "quality",
        "reciprocity",
        "initiated_by",
    ),
    "health_log": (
        "sleep_hours",
        "sleep_quality",
        "height_cm",
        "weight_kg",
        "exercise_minutes",
        "exercise_type",
        "diet_quality",
        "symptoms_json",
        "measures_json",
        "meds_note",
    ),
    "education_record": (
        "subject",
        "activity",
        "minutes",
        "score",
        "score_max",
        "skill_id",
    ),
    "career_record": (
        "activity",
        "minutes",
        "output_desc",
        "impact",
        "opportunity",
        "skill_id",
    ),
    "hobby_record": ("hobby_id", "minutes", "satisfaction", "progress_note"),
    "plan_record": (
        "plan_id",
        "planned_units",
        "actual_units",
        "unit",
        "output_value",
        "outcome",
    ),
    "idea_record": ("status", "spawned_plan_id", "spawned_goal_id"),
    "dream_record": ("clarity", "emotion", "themes_json", "recurring"),
}

# 意外类型的默认排除范围（第 5.2 节表）。写入时展开到 anomaly.excluded_metrics_json。
ANOMALY_TYPES: tuple[str, ...] = (
    "health",
    "finance",
    "relationship",
    "work",
    "environment",
    "plan_disruption",
    "other",
)

ANOMALY_DEFAULT_EXCLUSIONS: dict[str, list[str]] = {
    "health": ["health.*", "daily.energy", "daily.mood"],
    "finance": [
        "expense.total",
        "expense.by_category",
        "expense.nonessential_ratio",
        "savings_rate",
    ],
    "relationship": ["relation.*"],
    "work": ["career.*"],
    "environment": [],
    "plan_disruption": [],
    "other": [],
}

# 关系边（第 4.3 节 entry_link）
LINK_OBJECT_TYPES: tuple[str, ...] = ("entry", "goal", "plan", "person", "decision")
LINK_RELATIONS: tuple[str, ...] = (
    "caused_by",
    "affects",
    "part_of",
    "supports_goal",
    "inspired_by",
    "funded_by",
    "involves_person",
    "blocks",
    "follow_up_of",
)
LINK_CREATED_BY: tuple[str, ...] = ("user", "rule", "ai")

# entry 的状态与可见性（第 4.1 节）
ENTRY_STATUS: tuple[str, ...] = ("draft", "final")
ENTRY_VISIBILITY: tuple[str, ...] = ("private", "guardian_visible")

# ISO 4217 小数位（第 4.1 节 / ADR-14）
MINOR_EXP: dict[str, int] = {"CNY": 2, "JPY": 0, "KWD": 3}


def minor_exp_for(currency: str, fallback: int = 2) -> int:
    return MINOR_EXP.get(currency.upper(), fallback)


def default_sensitivity(domain: str, detail: dict[str, object] | None = None) -> int:
    """领域默认敏感度（第 4.4 节）。

    梦境原文与关系冲突细节为 3；健康症状为 2；收入开销为 1；其余为 1。
    """
    detail = detail or {}
    if domain == "dream":
        return 3
    if domain == "relation" and detail.get("kind") == "conflict":
        return 3
    if domain == "health":
        return 2 if detail.get("symptoms_json") else 1
    return 1
