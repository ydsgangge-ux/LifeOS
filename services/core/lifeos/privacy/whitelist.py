"""出站白名单（第 7.4 节第 2 步）：每种用途一份 YAML，只有列出的字段才能出站。

白名单是「允许清单」而不是黑名单：不在 allow 里的字段一律留在本地。
文件缺失即视为该用途未定义（第 7.5 节第 5 条：新增 purpose 必须同时补白名单与黄金测试）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

from .errors import UnknownPurposeError

# 已定义的出站用途；新增一项必须同时补 schemas/egress/<purpose>.yaml 与黄金测试
EGRESS_PURPOSES = (
    "weekly_report",
    "monthly_report",
    "yearly_report",
    "research_synthesis",
    "decision_review",
    # 页面级 AI 解读（第 16 章自我认知、第 11.1 节关系）
    "self_review",
    "relation_review",
)


@dataclass(frozen=True)
class Whitelist:
    """一份用途白名单。字段级 allow 清单，逐层收窄出站内容。"""

    purpose: str
    facts: frozenset[str]
    metrics: frozenset[str]
    metric_fields: frozenset[str]
    plan_fields: frozenset[str]
    plan_diagnosis_fields: frozenset[str]
    insight_fields: frozenset[str]
    anomaly_fields: frozenset[str]
    domain_fields: dict[str, frozenset[str]]
    deny_always: frozenset[str]
    transform: dict[str, str]
    free_text: bool = False
    # 通用区块 → 允许字段：决策解读这类非报告用途用它逐块收窄，
    # 不必为每种新用途再加一批专用字段。
    record_fields: dict[str, frozenset[str]] = field(default_factory=dict)


def _as_tuple(node: object) -> tuple[str, ...]:
    return tuple(str(item) for item in node) if isinstance(node, (list, tuple)) else ()


def _domain_fields(node: object) -> dict[str, frozenset[str]]:
    """领域摘要的逐领域字段清单：`domain: [字段, ...]`。"""
    if not isinstance(node, dict):
        return {}
    return {
        str(domain): frozenset(_as_tuple(fields))
        for domain, fields in node.items()
    }


def parse_whitelist(raw: dict[str, object], purpose: str) -> Whitelist:
    allow_raw = raw.get("allow")
    allow: dict[str, object] = allow_raw if isinstance(allow_raw, dict) else {}
    transform_raw = raw.get("transform")
    transform: dict[str, object] = transform_raw if isinstance(transform_raw, dict) else {}
    return Whitelist(
        purpose=str(raw.get("purpose") or purpose),
        facts=frozenset(_as_tuple(allow.get("facts"))),
        metrics=frozenset(_as_tuple(allow.get("metrics"))),
        metric_fields=frozenset(_as_tuple(allow.get("metric_fields"))),
        plan_fields=frozenset(_as_tuple(allow.get("plan_fields"))),
        plan_diagnosis_fields=frozenset(_as_tuple(allow.get("plan_diagnosis_fields"))),
        insight_fields=frozenset(_as_tuple(allow.get("insight_fields"))),
        anomaly_fields=frozenset(_as_tuple(allow.get("anomaly_fields"))),
        domain_fields=_domain_fields(allow.get("domain_facts")),
        deny_always=frozenset(_as_tuple(raw.get("deny_always"))),
        transform={str(key): str(value) for key, value in transform.items()},
        free_text=bool(allow.get("free_text", False)),
        record_fields=_domain_fields(allow.get("record_fields")),
    )


@cache
def load_whitelist(purpose: str, egress_dir: Path | str) -> Whitelist:
    """读取某用途的白名单；缺文件或缺用途定义都直接报错，不静默降级。"""
    if purpose not in EGRESS_PURPOSES:
        raise UnknownPurposeError(f"未知的出站用途：{purpose}")
    path = Path(egress_dir) / f"{purpose}.yaml"
    if not path.is_file():
        raise UnknownPurposeError(f"用途 {purpose} 缺少白名单文件：{path.name}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise UnknownPurposeError(f"白名单格式不合法：{path.name}")
    return parse_whitelist(raw, purpose)


def available_purposes(egress_dir: Path | str) -> list[str]:
    """目录里实际存在的用途文件，供界面下拉与自检使用。"""
    return sorted(path.stem for path in Path(egress_dir).glob("*.yaml"))
