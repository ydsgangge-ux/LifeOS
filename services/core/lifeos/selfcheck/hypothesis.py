"""假设生成与校验（第 16.5 / 16.6 / 16.7 节）。

默认路径完全不联网：用本地模板把检测器算出的差异写成提问句。模板文案写在
共享词表里（NFR-08），生成时按当前语言渲染后落库——假设是历史记录，落库即
成文，换语言不回改旧记录。

校验器强制第 16.6 节的三条底线：禁用词、替代解释至少 2 条且至少 1 条与
「用户的自述是对的」相容、证伪条件必填。数据不足时 build() 返回 None，
由调用方只输出数据缺口，不生成挑战性问题。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..domain.errors import InvalidInputError
from ..i18n import DEFAULT_LOCALE, t
from .detectors import DetectorResult

# 措辞底线（第 16.8 节）：评价或揣测性的词一律禁用
FORBIDDEN_WORDS = ("逃避", "借口", "否认", "你其实")

STATEMENT_KEYS = {
    "start_vs_finish": "selfcheck.hyp.start_vs_finish",
    "time_vs_values": "selfcheck.hyp.time_vs_values",
    "ability_vs_trial": "selfcheck.hyp.ability_vs_trial",
    "forecast_vs_history": "selfcheck.hyp.forecast_vs_history",
    "change_claim": "selfcheck.hyp.change_claim",
}

# 每个检测器至少两条替代解释，其中至少一条与「用户自述是对的」相容
ALT_KEYS: dict[str, list[tuple[str, bool]]] = {
    "start_vs_finish": [
        ("selfcheck.alt.start_vs_finish.1", False),
        ("selfcheck.alt.start_vs_finish.2", True),
    ],
    "time_vs_values": [
        ("selfcheck.alt.time_vs_values.1", False),
        ("selfcheck.alt.time_vs_values.2", True),
    ],
    "ability_vs_trial": [
        ("selfcheck.alt.ability_vs_trial.1", False),
        ("selfcheck.alt.ability_vs_trial.2", True),
    ],
    "forecast_vs_history": [
        ("selfcheck.alt.forecast_vs_history.1", False),
        ("selfcheck.alt.forecast_vs_history.2", True),
    ],
    "change_claim": [
        ("selfcheck.alt.change_claim.1", False),
        ("selfcheck.alt.change_claim.2", True),
    ],
}

FALSIFIER_KEYS = {
    "start_vs_finish": "selfcheck.falsifier.start_vs_finish",
    "time_vs_values": "selfcheck.falsifier.time_vs_values",
    "ability_vs_trial": "selfcheck.falsifier.ability_vs_trial",
    "forecast_vs_history": "selfcheck.falsifier.forecast_vs_history",
    "change_claim": "selfcheck.falsifier.change_claim",
}

# 生成时会用到的词表键（供 test_i18n 守卫检查，避免两端漂移）
USED_KEYS: tuple[str, ...] = tuple(
    [
        *STATEMENT_KEYS.values(),
        *FALSIFIER_KEYS.values(),
        *(key for pairs in ALT_KEYS.values() for key, _ in pairs),
    ]
)


def _tail(key: str) -> str:
    return key.rsplit(".", 1)[-1]


def _values(result: DetectorResult, locale: str) -> dict[str, Any]:
    """把检测器的事实与覆盖项摊平成模板变量（键名取 i18n 键的最后一段）。"""
    values: dict[str, Any] = {}
    for item in result.facts:
        values[_tail(str(item["key"]))] = item["value"]
    items = result.coverage.get("items") or []
    for item in items:
        values[_tail(str(item["key"]))] = item["value"]
    # 领域名换成当前语言的标签，模板里直接成句
    top = values.get("top_domain")
    if isinstance(top, str):
        values["top_domain_label"] = t(f"domain.{top}", locale)
        values["top_minutes"] = values.get(f"minutes_{top}")
    return values


def facts_hash(result: DetectorResult) -> str:
    """检测器输出的快照指纹（第 16.3 节 facts_snapshot_hash）。"""
    blob = json.dumps(result.to_json(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def validate(
    *,
    statement: str,
    alternatives: list[dict[str, Any]],
    falsifier: str,
) -> None:
    """三条底线校验；不合格直接抛 InvalidInputError（映射为 400）。"""
    if not statement.strip():
        raise InvalidInputError("假设陈述不能为空")
    blob = " ".join(
        [statement, falsifier, *(str(a.get("text", "")) for a in alternatives)]
    )
    for word in FORBIDDEN_WORDS:
        if word in blob:
            raise InvalidInputError(f"假设措辞不得包含「{word}」")
    if len(alternatives) < 2:
        raise InvalidInputError("假设至少需要 2 条替代解释")
    if not any(bool(a.get("compatible_with_claim")) for a in alternatives):
        raise InvalidInputError("替代解释中至少要有 1 条与「用户的自述是对的」相容")
    if not falsifier.strip():
        raise InvalidInputError("假设必须给出证伪条件")


def build(
    result: DetectorResult,
    *,
    locale: str = DEFAULT_LOCALE,
    claim_domain: str | None = None,
) -> dict[str, Any] | None:
    """数据门槛不够时返回 None（只出数据缺口），否则返回落库字段。"""
    if not result.sufficient or result.pattern_key not in STATEMENT_KEYS:
        return None
    values = _values(result, locale)
    if claim_domain:
        values["claim_domain_label"] = t(f"domain.{claim_domain}", locale)
        values["claim_minutes"] = values.get(f"minutes_{claim_domain}")
    params = values
    statement = t(STATEMENT_KEYS[result.pattern_key], locale, **params)
    alternatives = [
        {"text": t(key, locale, **params), "compatible_with_claim": compatible}
        for key, compatible in ALT_KEYS[result.pattern_key]
    ]
    falsifier = t(FALSIFIER_KEYS[result.pattern_key], locale, **params)
    validate(statement=statement, alternatives=alternatives, falsifier=falsifier)
    # 证据越实、覆盖越广，强度越高；门槛刚够用 weak，充分用 medium
    strength = "medium" if result.coverage.get("days", 0) >= 120 else "weak"
    return {
        "pattern_key": result.pattern_key,
        "statement": statement,
        "alternatives": alternatives,
        "falsifier": falsifier,
        "strength": strength,
        "coverage": result.coverage,
        "facts_snapshot_hash": facts_hash(result),
    }
