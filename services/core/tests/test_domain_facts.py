"""领域摘要（P1）：9 个没有可用指标的领域只统计过程事实，不评价内容。

守住三条：
- 数字可手算（次数、天数、时长、状态分布、转化数）；
- 不出现姓名、标题、正文、主题词原文、条目 id 或日期；
- 出站时按「领域 → 字段」白名单再裁一遍。
"""

from __future__ import annotations

import json
from datetime import date

from conftest import FastVault

from lifeos.analytics.domain_facts import build_domain_facts
from lifeos.config import AppConfig
from lifeos.privacy import POLICY_SUMMARY, load_whitelist, prepare
from lifeos.reporting.degrade import build_degraded

START, END = date(2026, 9, 28), date(2026, 10, 4)


def _entry(vault: FastVault, domain: str, day: str, detail: dict, **extra) -> dict:
    payload = {
        "domain": domain,
        "occurred_at": f"{day}T12:00:00+08:00",
        "detail": detail,
    }
    payload.update(extra)
    return vault.service.entries.create(payload)


def test_relation_summary_counts_without_names(fast_vault: FastVault) -> None:
    person = fast_vault.service.longterm.create_person(
        {"display_name": "张三", "note": "住址保密"}
    )
    _entry(
        fast_vault,
        "relation",
        "2026-09-29",
        {
            "person_id": person["id"],
            "kind": "meet",
            "quality": 4,
            "reciprocity": 1,
            "initiated_by": "me",
        },
        title="和张三吃饭",
        note="聊了私事",
    )
    _entry(
        fast_vault,
        "relation",
        "2026-09-30",
        {
            "person_id": person["id"],
            "kind": "conflict",
            "quality": 2,
            "reciprocity": -1,
            "initiated_by": "them",
        },
    )

    block = build_domain_facts(fast_vault.conn, START, END)["relation"]
    assert block["entries"] == 2
    assert block["active_days"] == 2
    assert block["person_count"] == 1
    assert block["kind_counts"] == {"conflict": 1, "meet": 1}
    assert block["initiated_counts"] == {"me": 1, "them": 1}
    assert block["quality_mean"] == 3.0
    assert block["reciprocity_mean"] == 0.0
    assert block["conflict_count"] == 1

    text = json.dumps(block, ensure_ascii=False)
    assert "张三" not in text
    assert "私事" not in text


def test_idea_summary_is_process_only(fast_vault: FastVault) -> None:
    plan = fast_vault.service.longterm.create_plan({"title": "做一个小工具"})
    _entry(
        fast_vault,
        "idea",
        "2026-09-28",
        {"status": "done", "spawned_plan_id": plan["id"]},
        title="做一个自动记账工具",
        note="灵感的原文不应出站",
    )
    _entry(fast_vault, "idea", "2026-09-29", {"status": "new"})
    _entry(fast_vault, "idea", "2026-09-30", {"status": "parked"})

    block = build_domain_facts(fast_vault.conn, START, END)["idea"]
    assert block["entries"] == 3
    assert block["status_counts"] == {
        "new": 1,
        "exploring": 0,
        "parked": 1,
        "dropped": 0,
        "done": 1,
    }
    assert block["open_count"] == 2
    assert block["converted_count"] == 1
    assert block["conversion_rate"] == 0.3333
    assert block["done_count"] == 1

    text = json.dumps(block, ensure_ascii=False)
    assert "自动记账" not in text
    assert "灵感" not in text


def test_dream_summary_hides_theme_text(fast_vault: FastVault) -> None:
    _entry(
        fast_vault,
        "dream",
        "2026-09-28",
        {
            "clarity": 4,
            "emotion": "紧张",
            "themes_json": ["飞行", "坠落"],
            "recurring": 1,
        },
        title="梦见在飞",
        note="很长的梦境原文",
    )
    _entry(
        fast_vault,
        "dream",
        "2026-09-29",
        {"clarity": 2, "themes_json": ["飞行"], "recurring": 0},
    )

    block = build_domain_facts(fast_vault.conn, START, END)["dream"]
    assert block["entries"] == 2
    assert block["active_days"] == 2
    assert block["clarity_mean"] == 3.0
    assert block["recurring_count"] == 1
    assert block["theme_count"] == 2
    assert block["theme_tagged_count"] == 2

    text = json.dumps(block, ensure_ascii=False)
    assert "飞行" not in text and "坠落" not in text
    assert "梦境原文" not in text and "紧张" not in text


def test_resource_summary_has_no_balances(fast_vault: FastVault) -> None:
    account = fast_vault.service.longterm.create_resource_account(
        {"name": "时间储备", "kind": "time", "unit": "小时", "initial_value": 40}
    )
    _entry(
        fast_vault,
        "resource",
        "2026-09-29",
        {"resource_id": account["id"], "delta": -2, "reason": "加班占用"},
    )
    _entry(
        fast_vault,
        "resource",
        "2026-09-30",
        {"resource_id": account["id"], "delta": 1.5, "reason": "早起"},
    )

    block = build_domain_facts(fast_vault.conn, START, END)["resource"]
    assert block["entries"] == 2
    assert block["account_count"] == 1
    assert block["kind_counts"] == {"time": 1}
    assert block["increase_events"] == 1
    assert block["decrease_events"] == 1
    assert block["unchanged_events"] == 0
    assert block["positive_accounts"] == 1

    text = json.dumps(block, ensure_ascii=False)
    assert "40" not in text  # 存量数值不出站
    assert "时间储备" not in text


def test_domain_facts_respect_visible_domains(fast_vault: FastVault) -> None:
    block = build_domain_facts(
        fast_vault.conn, START, END, domains=frozenset({"education", "hobby"})
    )
    assert set(block) == {"education", "hobby"}


def test_degraded_report_lists_domain_summaries() -> None:
    facts = {
        "metrics": [],
        "plans": [],
        "data_gaps": [],
        "domain_facts": {
            "relation": {
                "entries": 2,
                "active_days": 2,
                "person_count": 1,
                "conflict_count": 1,
            },
            "dream": {"entries": 0},
        },
    }
    summary, output = build_degraded(facts)
    assert [row["domain"] for row in output["domain_facts"]] == ["relation"]
    assert "关系：2 次互动" in summary
    # 没有记录的领域不占版面
    assert "梦境：" not in summary


def test_income_summary_never_exposes_amount(fast_vault: FastVault) -> None:
    """收入摘要只出条数与相对变化，绝对金额永不出站（第 7.4 节）。"""
    _entry(
        fast_vault,
        "income",
        "2026-09-29",
        {"amount": 12345, "currency": "CNY", "income_source": "salary"},
        title="九月工资",
    )
    _entry(
        fast_vault,
        "income",
        "2026-10-01",
        {"amount": 20000, "currency": "CNY", "income_source": "bonus"},
    )

    block = build_domain_facts(fast_vault.conn, START, END)["income"]
    assert block["entries"] == 2
    assert block["active_days"] == 2
    assert block["prev_entries"] == 0
    assert block["change_ratio"] is None  # 上期没有记录时不算比例
    assert set(block) == {"entries", "active_days", "prev_entries", "change_ratio"}

    text = json.dumps(block, ensure_ascii=False)
    assert "12345" not in text and "20000" not in text


def test_goal_summary_counts_without_titles(fast_vault: FastVault) -> None:
    """目标摘要只出状态与检查点计数，不出标题、why_note 与进展正文。"""
    goal = fast_vault.service.longterm.create_goal(
        {"title": "升职加薪", "why_note": "不想再加班", "horizon": "1y"}
    )
    _entry(
        fast_vault,
        "goal",
        "2026-09-30",
        {
            "goal_id": goal["id"],
            "metric_value": 3,
            "confidence": 3,
            "decision": "keep",
            "progress_note": "进展还不错",
        },
        title="季度回看",
    )

    block = build_domain_facts(fast_vault.conn, START, END)["goal"]
    assert block["goals_total"] == 1
    assert block["status_counts"] == {"active": 1}
    assert block["checkpoints"] == 1
    assert block["goals_with_checkpoint"] == 1
    assert block["decision_counts"] == {"keep": 1}
    assert block["metric_value_count"] == 1

    text = json.dumps(block, ensure_ascii=False)
    assert "升职加薪" not in text
    assert "不想再加班" not in text
    assert "进展还不错" not in text


def test_domain_facts_egress_filters_unlisted_fields(fast_vault: FastVault) -> None:
    """白名单按「领域 → 字段」收窄：没登记的字段直接丢弃，不靠「默认放行」。"""
    facts = {
        "facts_version": 2,
        "period": {"kind": "weekly", "start": "2026-09-28", "end": "2026-10-04"},
        "profile": {"age_band": "adult", "locale": "zh-CN"},
        "domain_facts_version": 1,
        "domain_facts": {
            "idea": {
                "entries": 3,
                "converted_count": 1,
                "status_counts": {"new": 1},
                "note": "不应出站的原文",
            }
        },
    }
    prepared = prepare(
        fast_vault.conn,
        facts=facts,
        whitelist=load_whitelist("weekly_report", AppConfig().egress_dir),
        policy=POLICY_SUMMARY,
        anchor=date(2026, 9, 7),
    )
    assert prepared.allowed
    assert prepared.payload["domain_facts_version"] == 1
    assert prepared.payload["domain_facts"] == {
        "idea": {
            "entries": 3,
            "converted_count": 1,
            "status_counts": {"new": 1},
        }
    }
