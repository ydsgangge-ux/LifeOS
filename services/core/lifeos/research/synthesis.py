"""决策报告合成（第 10.6 节）。

输入：评估结果（标准与权重、内部事实）、结构化证据。输出决策报告。

两条路径：
1. 确定性合成（默认）：把证据按权重排序、按标准归类，生成结构化报告。
   不依赖网络，可复现，未核实证据一律降权并标注。
2. LLM 合成（可选）：当出站策略允许且密钥齐备时，把结构化材料送模型润色；
   模型不可用时自动回退到确定性合成（第 9.6 节降级模式）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..domain.decisions import DecisionService
from .importer import evidence_weight

REPORT_VERSION = 1


def _quality_overview(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = defaultdict(int)
    unverified = 0
    stale = 0
    for e in evidence:
        by_type[e.get("source_type", "unknown")] += 1
        if not e.get("user_verified", False):
            unverified += 1
        if e.get("stale", False):
            stale += 1
    return {
        "total": len(evidence),
        "by_source_type": dict(by_type),
        "unverified": unverified,
        "stale": stale,
    }


def _options_matrix(
    evidence: list[dict[str, Any]],
    criteria: list[dict[str, Any]],
    internal_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把证据按标准归类，生成选项矩阵。

    v1 只有一列「证据」（选项矩阵的「选项」由用户在向导里补充），每格引用
    evidence id 或内部事实 key；没有依据的格子写「无依据」。
    """
    criteria_keys = {c["key"]: c["name"] for c in criteria}
    by_criterion: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in evidence:
        weight = evidence_weight(e)
        by_criterion[e.get("task_id", "")].append(
            {
                "evidence_id": e["id"],
                "claim": e["claim"],
                "weight": weight,
                "verified": bool(e.get("user_verified", False)),
                "stale": bool(e.get("stale", False)),
            }
        )
    rows: list[dict[str, Any]] = []
    for ckey, cname in criteria_keys.items():
        items = by_criterion.get(ckey, [])
        items.sort(key=lambda x: x["weight"], reverse=True)
        if not items:
            items = [
                {
                    "evidence_id": None,
                    "claim": "无依据",
                    "weight": 0.0,
                    "verified": False,
                    "stale": False,
                }
            ]
        rows.append(
            {
                "criterion_key": ckey,
                "criterion_name": cname,
                "items": items,
            }
        )
    if internal_facts:
        rows.append(
            {
                "criterion_key": "_internal",
                "criterion_name": "内部事实（本地）",
                "items": [
                    {
                        "evidence_id": f["key"],
                        "claim": f"{f['description']}: {f['value']}",
                        "weight": 1.0,
                        "verified": True,
                        "stale": False,
                    }
                    for f in internal_facts
                ],
            }
        )
    return rows


def _key_assumptions(evidence: list[dict[str, Any]]) -> list[str]:
    """从高权重证据里提炼关键假设（确定性，不做推断）。"""
    weighted = sorted(evidence, key=evidence_weight, reverse=True)
    assumptions: list[str] = []
    for e in weighted[:5]:
        tag = "（未核实）" if not e.get("user_verified", False) else ""
        stale_tag = "（可能过期）" if e.get("stale", False) else ""
        assumptions.append(f"{e['claim']}{tag}{stale_tag}")
    return assumptions or ["暂无足够证据形成关键假设"]


def synthesize(
    decisions: DecisionService,
    case_id: str,
) -> dict[str, Any]:
    """生成决策报告。"""
    case = decisions.get_case(case_id)
    assessment = case.get("assessment_json") or {}
    criteria = assessment.get("criteria", [])
    internal_facts = assessment.get("internal_facts", [])
    risk_level = assessment.get("risk_level", "medium")

    evidence = decisions.list_evidence(case_id)
    quality = _quality_overview(evidence)
    matrix = _options_matrix(evidence, criteria, internal_facts)
    assumptions = _key_assumptions(evidence)

    unverified_ids = [e["id"] for e in evidence if not e.get("user_verified", False)]

    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "case_id": case_id,
        "question": case["question"],
        "risk_level": risk_level,
        "evidence_quality": quality,
        "options_matrix": matrix,
        "key_assumptions": assumptions,
        "reversible_experiments": [
            "用 4 周时间做最小可行实验，验证最高权重假设是否成立",
        ],
        "conflicting_evidence": [
            {"evidence_id": e["id"], "conflicts_with": e.get("conflicts_with")}
            for e in evidence
            if e.get("conflicts_with")
        ],
        "missing_information": [
            gap["question"]
            for gap in assessment.get("knowledge_gaps", [])
            if not any(gap["id"] == e.get("task_id") for e in evidence)
        ],
        "unverified_evidence_ids": unverified_ids,
        "downweight_note": (
            f"共 {quality['unverified']} 条未核实证据已降权（权重×0.5），"
            f"{quality['stale']} 条过期证据再降权（权重×0.6），合成结论仅供参考。"
        ),
    }
    if risk_level == "high":
        report["high_risk_notice"] = (
            "本决策风险等级为 high，系统只给出「如何降低不确定性」的步骤，"
            "不给出结论性推荐。请先核实关键证据。"
        )
    return report
