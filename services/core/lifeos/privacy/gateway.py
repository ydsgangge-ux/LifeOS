"""隐私网关（第 7.4 节）：唯一联网出口的前置闸门，报告与联网研究都走这里。

管线八步：选择 → 白名单 → 泛化 → 自由文本 → 预览 → 记录 → 调用 → 返回。
本模块负责前六步里「本地能算完」的部分，产出 `Prepared`（可出站的载荷 + 脱敏报告
+ 哈希）；第七步的真实调用由 llm 模块做，它只允许发送这里准备好的载荷。

两道保险叠加：

1. 白名单是允许清单：不在 allow 里的区块与字段一律不出现（第 7.4 节第 2 步）。
2. 逐字段扫描兜底：任何字段（含键名）一旦扫出标识，就整字段丢弃并记入报告，
   而不是「发现了但照发」——第 7.5 节要求黄金测试 0 泄漏。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..db.connection import VaultConnection
from ..i18n import DEFAULT_LOCALE, message, render, resolve_ref, t
from .audit import payload_digest
from .generalize import person_names, relative_money, truncate, week_index
from .policy import POLICY_EXTENDED, POLICY_NONE
from .scan import scan_text
from .whitelist import Whitelist

# 事实包（第 8.7 节）里可被白名单选择的区块，顺序即出站 JSON 的键序
_FACT_BLOCKS = (
    "facts_version",
    "period",
    "profile",
    "coverage",
    "metrics",
    "domain_facts_version",
    "domain_facts",
    "plans",
    "insights",
    "insights_version",
    "anomalies",
    "data_gaps",
    # 跨月对比（monthly/yearly）：逐月聚合数字，字段由 record_fields 收窄
    "history",
    # 决策解读（decision_review）：只发结构化材料，不发人物与原文
    "decision_case",
    "decision_criteria",
    "decision_internal_facts",
    "decision_evidence",
    "decision_quality",
    # 自我认知解读（self_review）：自述与假设文字 + 计数，不发日记原文与备注
    "self_summary",
    "self_claims",
    "self_hypotheses",
    # 关系解读（relation_review）：人物代号 + 聚合数字，不发姓名与单条互动
    "relation_overview",
    "relation_people",
    "relation_months",
)

_TRANSFORM_WEEK = "week_index"
_TRANSFORM_MONEY = "relative_to_monthly_income"

# 金额类指标在票面单位上判定；value_kind 缺失时仍能兜住
_MONEY_UNITS = frozenset({"元", "CNY", "¥", "美元", "USD"})
_MONEY_FIELDS = ("value", "prev_value", "delta", "value_without_exclusion")

_DROP = object()


@dataclass(frozen=True)
class Prepared:
    """一次出站尝试的结果：要么可发（allowed），要么被策略拦下。"""

    purpose: str
    policy: str
    allowed: bool
    payload: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    reason: dict[str, Any] | None = None
    sha256: str = ""
    nbytes: int = 0


def prepare(
    conn: VaultConnection,
    *,
    facts: dict[str, Any],
    whitelist: Whitelist,
    policy: str,
    anchor: date,
    income_value: float | None = None,
    confirmed_texts: Sequence[str] | None = None,
) -> Prepared:
    """把事实包压成「可以出站的最小载荷」。

    ``confirmed_texts`` 只在 api_extended 下才会被考虑：那是用户在界面上逐条确认过的
    摘要（第 7.4 节第 4 步），仍然要过一遍标识扫描，扫出标识的那条直接丢弃。
    """
    if policy == POLICY_NONE:
        # 不出站也要留痕：记录一次 blocked，便于核对「谁试图发什么」
        empty: dict[str, Any] = {}
        digest, nbytes = payload_digest(empty)
        return Prepared(
            purpose=whitelist.purpose,
            policy=policy,
            allowed=False,
            reason=message("egress.blocked.policy_none"),
            sha256=digest,
            nbytes=nbytes,
        )

    names = person_names(conn)
    transforms = {"money_to_ratio": 0, "money_dropped": 0, "date_to_week": 0}
    dropped: list[str] = []
    hits: dict[str, int] = {}

    payload: dict[str, Any] = {}
    for block in _FACT_BLOCKS:
        if block not in whitelist.facts or block not in facts:
            continue
        value = facts[block]
        if block == "period":
            value = _transform_period(value, whitelist, anchor, transforms)
        elif block == "metrics":
            value = _filter_metrics(
                value, whitelist, income_value, transforms, dropped
            )
        elif block == "domain_facts":
            value = _filter_domain_facts(value, whitelist)
        elif block == "plans":
            value = _filter_plans(value, whitelist)
        elif block == "insights":
            value = _filter_records(value, whitelist.insight_fields)
        elif block == "anomalies":
            value = _filter_mapping(value, whitelist.anomaly_fields)
        elif block in whitelist.record_fields:
            # 通用区块：字段级收窄，dict 按允许键过滤，list 逐行过滤
            allowed = whitelist.record_fields[block]
            value = (
                _filter_records(value, allowed)
                if isinstance(value, list)
                else _filter_mapping(value, allowed)
            )
        payload[block] = value

    # 自由文本要同时满足：白名单允许、策略是 extended、且用户逐条确认过
    if confirmed_texts and policy == POLICY_EXTENDED and whitelist.free_text:
        payload["confirmed_texts"] = list(confirmed_texts)

    # 出站前把 i18n 引用与消息对象渲染成目标语言的文字：模型读的是人话，
    # 而不是 i18n:metric.* 这类键（第 7.4 节第 1 步、NFR-08）。
    payload = _localize(payload, _payload_locale(facts))
    payload = _scrub(payload, "root", names=names, dropped=dropped, hits=hits)

    report = {
        "dropped": dropped,
        "dropped_count": len(dropped),
        "hits": hits,
        "transforms": transforms,
    }
    digest, nbytes = payload_digest(payload)
    return Prepared(
        purpose=whitelist.purpose,
        policy=policy,
        allowed=True,
        payload=payload,
        report=report,
        sha256=digest,
        nbytes=nbytes,
    )


# -- 区块级筛选 ---------------------------------------------------------------
def _filter_metrics(
    items: Any,
    whitelist: Whitelist,
    income_value: float | None,
    transforms: dict[str, int],
    dropped: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("key") not in whitelist.metrics:
            continue
        path = f"metrics.{item.get('key')}"
        if _is_money(item):
            for name in _MONEY_FIELDS:
                raw = _as_float(item.get(name))
                if name not in item or raw is None:
                    continue  # 本来就是空值（样本不足），保持原样
                ratio = relative_money(raw, income_value)
                if ratio is None:
                    # 没有可用分母：把字段整个摘掉，不留「半截」数字
                    item = {key: value for key, value in item.items() if key != name}
                    transforms["money_dropped"] += 1
                    dropped.append(f"{path}.{name}")
                else:
                    item = {**item, name: ratio}
                    transforms["money_to_ratio"] += 1
            # 值已经是「占收入比例」，单位与类型必须跟着改：否则模型会把 0.35
            # 读成 0.35 元（第 7.4 节第 3 步）。
            item = {
                **item,
                "value_kind": "ratio",
                "unit": "i18n:egress.unit.income_share",
            }
        item = _sanitize_lineage(item)
        row = {key: value for key, value in item.items() if key in whitelist.metric_fields}
        out.append(row)
    return out


def _filter_domain_facts(value: Any, whitelist: Whitelist) -> dict[str, Any]:
    """领域摘要按「领域 → 字段」双重收窄；没登记的领域整块丢弃。"""
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for domain in sorted(whitelist.domain_fields):
        item = value.get(domain)
        if not isinstance(item, dict):
            continue
        allowed = whitelist.domain_fields[domain]
        out[domain] = {
            key: field for key, field in item.items() if key in allowed
        }
    return out


def _filter_plans(items: Any, whitelist: Whitelist) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = {key: value for key, value in item.items() if key in whitelist.plan_fields}
        diagnosis = row.get("diagnosis")
        if isinstance(diagnosis, dict):
            row["diagnosis"] = {
                key: value
                for key, value in diagnosis.items()
                if key in whitelist.plan_diagnosis_fields
            }
        out.append(row)
    return out


def _filter_records(items: Any, allowed: frozenset[str]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [
        {key: value for key, value in item.items() if key in allowed}
        for item in items
        if isinstance(item, dict)
    ]


def _filter_mapping(value: Any, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key in allowed}


def _transform_period(
    period: Any, whitelist: Whitelist, anchor: date, transforms: dict[str, int]
) -> dict[str, Any]:
    if not isinstance(period, dict):
        return {}
    if whitelist.transform.get("date") != _TRANSFORM_WEEK:
        return dict(period)
    out: dict[str, Any] = {"kind": period.get("kind")}
    for source, target in (("start", "start_week"), ("end", "end_week")):
        day = _as_date(period.get(source))
        if day is not None:
            out[target] = week_index(day, anchor)
            transforms["date_to_week"] += 1
    return out


# -- 出站前的本地化与血缘瘦身 ------------------------------------------------
def _payload_locale(facts: dict[str, Any]) -> str:
    profile = facts.get("profile")
    if isinstance(profile, dict):
        locale = profile.get("locale")
        if isinstance(locale, str) and locale:
            return locale
    return DEFAULT_LOCALE


def _sanitize_lineage(item: dict[str, Any]) -> dict[str, Any]:
    """把沾染具体日期与条目 id 的血缘裁成模型能用的形状。

    ``lineage.excluded`` 里带 entry_id 与精确日期，``corrections`` 里带纠正记录 id
    与日期；这些都不该出站（日期泛化只做 period 是不够的）。保留聚合后的
    ``corrected`` 布尔值，本地阅读器仍能标出「已按你的纠正」。
    """
    lineage = item.get("lineage")
    if not isinstance(lineage, dict):
        return item
    clean = {
        key: value
        for key, value in lineage.items()
        if key not in ("excluded", "corrections")
    }
    clean["corrected"] = bool(lineage.get("corrections"))
    return {**item, "lineage": clean}


def _localize(value: Any, locale: str) -> Any:
    """递归把 ``i18n:<key>`` 引用与 ``{key, params}`` 消息对象渲染成目标语言文字。"""
    if isinstance(value, str):
        return resolve_ref(value, locale)
    if isinstance(value, dict):
        if set(value) == {"key", "params"} and isinstance(value.get("key"), str):
            params = value.get("params")
            return render(
                {"key": value["key"], "params": _localize_params(params, locale)},
                locale,
            )
        return {key: _localize(item, locale) for key, item in value.items()}
    if isinstance(value, list):
        return [_localize(item, locale) for item in value]
    return value


def _localize_params(params: Any, locale: str) -> dict[str, Any]:
    """消息参数里的领域/指标代号换成对应语言的名称（``relation`` → ``关系``）。"""
    if not isinstance(params, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in params.items():
        if isinstance(item, str):
            label = t(f"{key}.{item}", locale)
            out[key] = item if label == f"{key}.{item}" else label
        else:
            out[key] = item
    return out


# -- 逐字段扫描（兜底） -------------------------------------------------------
def _scrub(
    value: Any,
    path: str,
    *,
    names: Sequence[str],
    dropped: list[str],
    hits: dict[str, int],
) -> Any:
    """递归扫描；命中即丢字段（不是命中就整包拒绝，避免一处误伤毁掉整份报告）。"""
    if isinstance(value, str):
        return value if _clean(value, path, names, dropped, hits) else _DROP
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and not _clean(key, path, names, dropped, hits):
                continue
            scrubbed = _scrub(item, f"{path}.{key}", names=names, dropped=dropped, hits=hits)
            if scrubbed is not _DROP:
                out[key] = scrubbed
        return out
    if isinstance(value, list):
        items: list[Any] = []
        for index, item in enumerate(value):
            scrubbed = _scrub(
                item, f"{path}[{index}]", names=names, dropped=dropped, hits=hits
            )
            if scrubbed is not _DROP:
                items.append(scrubbed)
        return items
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if _clean(str(value), path, names, dropped, hits) else _DROP
    return value


def _clean(
    text: str, path: str, names: Sequence[str], dropped: list[str], hits: dict[str, int]
) -> bool:
    report = scan_text(text, names=names)
    if report.clean:
        return True
    dropped.append(path)
    for kind, count in report.counts.items():
        hits[kind] = hits.get(kind, 0) + count
    return False


# -- 小工具 -------------------------------------------------------------------
def _is_money(item: dict[str, Any]) -> bool:
    if item.get("value_kind") == "money":
        return True
    return item.get("value_kind") is None and str(item.get("unit")) in _MONEY_UNITS


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def monthly_income_value(
    analytics: Any, *, kind: str, end: str | date | None, start: str | date | None
) -> float | None:
    """金额泛化的分母：本周期收入总额，只在本机算，不进载荷（第 7.4 节第 3 步）。"""
    block = analytics.metrics(kind=kind, end=end, start=start, keys=["income.total"])
    items = block.get("items") or []
    if not items:
        return None
    return _as_float(items[0].get("value"))


__all__ = ["Prepared", "monthly_income_value", "prepare", "truncate"]
