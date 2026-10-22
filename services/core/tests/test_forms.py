"""表单加载、提交映射与动态校验（第 6 章 / 第 14 章 P0 测试清单）。"""

from __future__ import annotations

import pytest

from lifeos.config import AppConfig
from lifeos.domain import schema_defs as sd
from lifeos.domain.errors import InvalidInputError
from lifeos.forms.loader import _INT_CHOICE_TARGETS, FormRegistry
from lifeos.forms.pydantic_builder import build_model, validate_submission

EXPECTED_FORMS = {
    "daily.mood",
    "expense.spend",
    "health.daily",
    "plan.checkin",
    "income.receive",
    "resource.check",
    "relation.interaction",
    "education.session",
    "career.log",
    "hobby.session",
    "idea.capture",
    "dream.record",
    "goal.review",
}

_ENTRY_TARGETS = {"entry.title", "entry.note", "entry.tags"}


@pytest.fixture
def registry() -> FormRegistry:
    return FormRegistry(AppConfig().forms_dir)


def test_config_points_at_repo_schemas(registry: FormRegistry) -> None:
    assert registry.forms_dir.name == "forms"
    assert registry.forms_dir.exists()


def test_loads_example_forms(registry: FormRegistry) -> None:
    assert {f.id for f in registry.all()} >= EXPECTED_FORMS
    form = registry.get("expense.spend")
    assert form.domain == "expense"
    assert form.anomaly is True
    assert [s.id for s in form.sections] == ["amount", "context"]
    assert registry.get("health.daily").fields[0].type == "number"
    assert registry.get("health.daily").fields[0].minimum == 0
    assert registry.get("health.daily").fields[0].maximum == 24


def test_every_form_target_is_writable(registry: FormRegistry) -> None:
    """第 5 章：字段只能写本领域详情表的白名单列，写错列不会报错而是静默丢弃。"""
    for form in registry.all():
        table = sd.DOMAIN_DETAIL_TABLE[form.domain]
        for fdef in form.fields:
            assert fdef.target, f"{form.id}.{fdef.key} 缺少 target"
            if fdef.target in _ENTRY_TARGETS:
                continue
            prefix, _, column = fdef.target.partition(".")
            assert prefix == table, f"{form.id}.{fdef.key} 指向了别的表：{fdef.target}"
            assert column in sd.DETAIL_COLUMNS[table], (
                f"{form.id}.{fdef.key} 的列不在白名单：{fdef.target}"
            )


def test_unknown_form_and_type_are_rejected() -> None:
    with pytest.raises(InvalidInputError):
        FormRegistry(AppConfig().forms_dir).get("nope.none")


def test_money_mapping_uses_currency_minor_exp(registry: FormRegistry, fast_vault) -> None:
    payload = registry.to_entry_payload(
        "expense.spend", {"amount": 12.34, "currency": "CNY", "category": "food", "title": "午餐"}
    )
    assert payload["domain"] == "expense"
    assert payload["detail"]["amount_minor"] == 1234
    assert payload["title"] == "午餐"

    entry = fast_vault.service.entries.create(payload)
    assert entry["detail"]["amount_minor"] == 1234
    assert entry["detail"]["minor_exp"] == 2  # 由币种推导

    jpy = registry.to_entry_payload("expense.spend", {"amount": 1000, "currency": "JPY"})
    assert jpy["detail"]["amount_minor"] == 1000
    kwd = registry.to_entry_payload("expense.spend", {"amount": 1.234, "currency": "KWD"})
    assert kwd["detail"]["amount_minor"] == 1234


def test_bool_choice_targets_become_int(registry: FormRegistry) -> None:
    assert "txn.essential" in _INT_CHOICE_TARGETS
    payload = registry.to_entry_payload(
        "expense.spend", {"amount": 5, "currency": "CNY", "essential": "1", "recurring": 0}
    )
    assert payload["detail"]["essential"] == 1
    assert payload["detail"]["recurring"] == 0


def test_anomaly_block_passes_through(registry: FormRegistry) -> None:
    payload = registry.to_entry_payload(
        "expense.spend",
        {
            "amount": 5,
            "currency": "CNY",
            "anomaly": {"type": "finance", "impact": 3, "one_off": True},
        },
    )
    assert payload["anomalies"][0]["type"] == "finance"


def test_unknown_field_goes_to_extra(registry: FormRegistry) -> None:
    payload = registry.to_entry_payload(
        "daily.mood", {"mood": 4, "custom_key": "自定义值", "title": "今天"}
    )
    assert payload["detail"]["mood"] == 4
    assert payload["extra"]["custom_key"] == "自定义值"


def test_age_band_filter(registry: FormRegistry) -> None:
    assert {f.id for f in registry.for_age_band("adult")} >= EXPECTED_FORMS


def test_pydantic_model_accepts_partial_and_unknown(registry: FormRegistry) -> None:
    form = registry.get("health.daily")
    model = build_model(form)
    instance = model.model_validate({"sleep_hours": 7.5, "symptoms": ["头痛"], "whatever": 1})
    assert instance.sleep_hours == 7.5
    assert instance.symptoms == ["头痛"]
    assert instance.whatever == 1  # extra="allow"

    values = validate_submission(form, {"sleep_hours": 7.5})
    assert values["sleep_hours"] == 7.5
    assert values["diet_quality"] is None

    with pytest.raises(ValueError):
        model.model_validate({"sleep_hours": 30})  # 超出 YAML 的 max: 24
