"""个人基础信息（0010 迁移）：读写、校验，以及进入决策内部事实。"""

from __future__ import annotations

import pytest
from conftest import build_vault

from lifeos.config import AppConfig
from lifeos.domain import basics as basics_mod
from lifeos.domain.errors import InvalidInputError
from lifeos.research import evaluator as evaluator_mod


@pytest.fixture
def vault(tmp_path):
    built = build_vault(tmp_path, "basics")
    yield built
    built.conn.close()


def test_basics_roundtrip_and_template_facts(vault) -> None:
    basics_mod.update_basics(
        vault.conn,
        {
            "job_title": "后端工程师",
            "industry": "tech",
            "education_level": "bachelor",
            "work_style": "flexible",
            "risk_tolerance": "balanced",
            "family_status": "married",
            "dependents": 1,
            "constraints": ["commute", "time"],
        },
    )
    data = basics_mod.read_basics(vault.conn)
    assert data["industry"] == "tech"
    assert data["constraints"] == ["commute", "time"]
    assert data["updated_at"]

    facts = basics_mod.facts_for_templates(vault.conn)
    assert "中等" in facts["risk_tolerance"]
    assert "已婚" in facts["family_constraints"]
    assert "1 人" in facts["family_constraints"]
    assert "通勤" in facts["work_constraints"]

    assessment = evaluator_mod.evaluate(
        vault.conn, "我想创业", AppConfig().research_templates_dir, decision_type="venture"
    )
    values = {item["key"]: item["value"] for item in assessment["internal_facts"]}
    # 模板问到的字段直接引用基础信息，不再显示「未登记」
    assert values["risk_tolerance"] == facts["risk_tolerance"]
    assert values["family_constraints"] == facts["family_constraints"]


def test_basics_reject_invalid_values(vault) -> None:
    with pytest.raises(InvalidInputError):
        basics_mod.update_basics(vault.conn, {"risk_tolerance": "reckless"})
    with pytest.raises(InvalidInputError):
        basics_mod.update_basics(vault.conn, {"constraints": ["unknown"]})
    with pytest.raises(InvalidInputError):
        basics_mod.update_basics(vault.conn, {"dependents": 9})
