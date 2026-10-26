"""reporting 模块测试（第 9.2 / 9.3 / 9.6 / 9.7 节、P1 验收）。

覆盖四块：
- 校验器：第 9.3 节八条规则逐条参数化。
- 占位符渲染：数值由程序填、被纠正过的字段追加标记。
- 流水线：正常生成（假 transport 注入模型）、无出口时自动降级、结论可追溯到事实包。
- 调度：该跑哪个周期、到点未生成才补跑；小实验采纳成 once 计划。

模型一律用 httpx.MockTransport 伪造，测试不真的联网（第 9.6 节「关闭网络后可用」）。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import httpx
import pytest
from conftest import FastVault

from lifeos.analytics.service import AnalyticsService
from lifeos.config import AppConfig
from lifeos.domain.errors import InvalidInputError
from lifeos.llm import gateway as llm_gateway
from lifeos.privacy import load_whitelist, write_policy
from lifeos.reporting import (
    adopt_experiment,
    build_child_report,
    generate_report,
    list_reports,
    load_prompt,
    missing_periods,
    monthly_due_end,
    render_output,
    resolve_ref,
    validate_output,
    weekly_due_end,
)
from lifeos.reporting.agents import (
    _MAX_ENTRY_TEXTS_PER_DOMAIN,
    _source_issues,
    compact_domain_facts,
    integration_facts,
    source_digest,
)
from lifeos.reporting.degrade import build_degraded
from lifeos.reporting.render import render_claim
from lifeos.reporting.store import reports_in_period, save_report
from lifeos.reporting.validator import BANNED_ALWAYS, BANNED_CHILD

PROVIDER = "openai"
MODEL = "gpt-4o-mini"
BASE_URL = "https://api.example/v1"

# 一份最小事实包：一个达门槛的指标、一个不达门槛的指标、一个计划、一条相关性提示
PAYLOAD = {
    "metrics": [
        {
            "key": "health.sleep.mean_hours",
            "value": 7.5,
            "prev_value": 7.0,
            "delta": 0.5,
            "n": 6,
            "unit": "小时",
            "value_kind": "number",
            "lineage": {"corrections": []},
        },
        {
            "key": "expense.total",
            "value": 0.2,
            "prev_value": 0.18,
            "delta": 0.02,
            "n": 1,
            "unit": "元",
            "value_kind": "money",
            "lineage": {"corrections": [{"id": "c1"}]},
        },
    ],
    "plans": [{"ref": "plan_A", "diagnosis": {"verdict": "keep"}}],
    "insights": [{"id": "ins_01", "rho": 0.5}],
    "data_gaps": [{"key": "facts.gap.domain_empty", "params": {"domain": "dream"}}],
}
MIN_N = {"health.sleep.mean_hours": 4, "expense.total": 1}


def _finding(**overrides):
    base = {
        "claim": "睡眠与上周相比有变化 [[fact:health.sleep.mean_hours]]",
        "figures": [{"ref": "health.sleep.mean_hours", "format": "hours"}],
        "evidence_refs": ["health.sleep.mean_hours"],
        "strength": "medium",
        "causal_claim": False,
        "alternatives": ["可能是记录时点变化引起的"],
    }
    base.update(overrides)
    return base


def _validate(finding, **kwargs):
    payload = kwargs.pop("payload", PAYLOAD)
    output = {"highlights": [finding]}
    return validate_output(
        output, payload=payload, min_n_by_key=MIN_N, age_band=kwargs.pop("age_band", "adult")
    )


def _rules(result) -> set[int]:
    return {issue.rule for issue in result.issues}


# ---------------------------------------------------------------------------
# 校验器：第 9.3 节八条规则
# ---------------------------------------------------------------------------
def test_baseline_finding_passes() -> None:
    assert _validate(_finding()).ok


def test_rule1_evidence_refs_must_exist() -> None:
    assert 1 in _rules(_validate(_finding(evidence_refs=[])))
    assert 1 in _rules(_validate(_finding(evidence_refs=["health.sleep.nope"])))


def test_rule2_no_raw_numbers_outside_placeholders() -> None:
    assert 2 in _rules(_validate(_finding(claim="本周睡了 7 个小时")))
    assert 2 in _rules(_validate(_finding(claim="支出占 40%")))
    # 占位符里的键名不算数字，替换后也不该误判
    assert _validate(_finding(claim="睡眠 [[fact:health.sleep.mean_hours]]")).ok


def test_rule2_placeholder_must_resolve() -> None:
    """字段键写错的占位符会让渲染把 [[fact:...]] 原样显示，必须在校验阶段拦下。"""
    result = _validate(_finding(claim="关系 [[fact:relation.entries]] 条"))
    assert 2 in _rules(result)
    assert any("relation.entries" in issue.message for issue in result.issues)


def test_rule3_strong_requires_min_n() -> None:
    strong = _finding(
        strength="strong",
        evidence_refs=["metrics.health.sleep.mean_hours"],
        claim="睡眠达标 [[fact:health.sleep.mean_hours]]",
    )
    assert _validate(strong).ok  # n=6 ≥ 4，达门槛，不该误报
    # 把 n 调低到 2（< min_n 4）才是规则 3 的触发条件
    payload = {
        **PAYLOAD,
        "metrics": [
            {**PAYLOAD["metrics"][0], "n": 2},
            *PAYLOAD["metrics"][1:],
        ],
    }
    assert 3 in _rules(
        _validate(
            _finding(
                strength="strong",
                evidence_refs=["health.sleep.mean_hours"],
                claim="睡眠 [[fact:health.sleep.mean_hours]]",
            ),
            payload=payload,
        )
    )
    # 相关性类不得 strong
    assert 3 in _rules(
        _validate(
            _finding(
                strength="strong",
                claim="两者同时出现",
                evidence_refs=["insights.ins_01.rho"],
                alternatives=["可能是共同因素"],
            )
        )
    )


def test_rule4_alternatives_are_optional() -> None:
    """方案放开：没有像样的替代解释时可以省略；写了空串或非数组才拦。"""
    assert _validate(_finding(alternatives=[])).ok
    assert 4 not in _rules(_validate({k: v for k, v in _finding().items() if k != "alternatives"}))
    assert 4 in _rules(_validate(_finding(alternatives=["", "  "])))
    assert 4 in _rules(_validate(_finding(alternatives="可能是记录时点变化")))


def test_rule5_banned_phrases() -> None:
    assert 5 in _rules(_validate(_finding(claim="你的性格是内向")))
    # 儿童档额外禁用「排名」
    assert 5 in _rules(_validate(_finding(claim="本次排名靠后"), age_band="child"))
    assert _validate(_finding(claim="本次排名靠后"), age_band="adult").ok


def test_rule6_non_causal_must_not_use_causal_words() -> None:
    assert 6 in _rules(_validate(_finding(claim="熬夜导致了睡眠变差")))
    # 显式声明的因果结论不触发
    assert _validate(_finding(claim="熬夜导致了睡眠变差", causal_claim=True)).ok


def test_rule7_only_codes_from_facts() -> None:
    assert 7 in _rules(_validate(_finding(claim="计划 plan_Z 未完成")))
    assert _validate(_finding(claim="计划 plan_A 达标")).ok


def test_empty_output_is_rejected() -> None:
    result = validate_output({}, payload=PAYLOAD, min_n_by_key=MIN_N)
    assert not result.ok and 1 in _rules(result)


# ---------------------------------------------------------------------------
# 占位符渲染（第 9.3 节第 2、8 条）
# ---------------------------------------------------------------------------
def test_render_replaces_placeholder_with_program_value() -> None:
    text = render_claim(
        "睡眠 [[fact:health.sleep.mean_hours]]",
        [{"ref": "health.sleep.mean_hours", "format": "hours"}],
        PAYLOAD,
        set(),
    )
    assert text == "睡眠 7.5 小时"


def test_money_metric_renders_raw_amount_with_unit_and_correction_marker() -> None:
    payload = {
        "metrics": [
            {
                "key": "expense.total",
                "value": 2413.0,
                "value_kind": "money",
                "unit": "元",
                "lineage": {"corrections": [{"id": "c1"}]},
            },
        ]
    }
    output = {
        "highlights": [
            {
                "claim": "本期支出 [[fact:expense.total]]",
                "figures": [{"ref": "expense.total"}],
                "evidence_refs": ["expense.total"],
                "strength": "medium",
                "alternatives": ["基数较小"],
            }
        ]
    }
    rendered = render_output(output, payload)
    claim = rendered["highlights"][0]["rendered_claim"]
    assert claim == "本期支出 2,413 元（已按你的纠正）"


def test_money_metric_renders_raw_amount_not_share_of_income() -> None:
    """报告用途的金额原值出站，本地渲染同样按票面金额，不再换算成占收入比例。"""
    payload = {
        "metrics": [
            {"key": "expense.total", "value": 2413.0, "value_kind": "money", "unit": "元"},
            {"key": "income.total", "value": 13000.0, "value_kind": "money", "unit": "元"},
        ]
    }
    claim = render_claim("本期支出 [[fact:metrics.expense.total.value]]", [], payload, set())
    assert claim == "本期支出 2,413 元"


def test_money_metric_without_income_still_renders_amount() -> None:
    """金额不再依赖收入做分母，没有收入记录时也要照常出数。"""
    payload = {
        "metrics": [
            {"key": "expense.total", "value": 2413.0, "value_kind": "money", "unit": "元"},
        ]
    }
    assert render_claim("支出 [[fact:expense.total]]", [], payload, set()) == "支出 2,413 元"


def test_income_total_renders_as_raw_amount() -> None:
    payload = {
        "metrics": [
            {"key": "income.total", "value": 13000.0, "value_kind": "money", "unit": "元"},
        ]
    }
    assert render_claim("收入 [[fact:income.total]]", [], payload, set()) == "收入 13,000 元"


def test_money_metric_metadata_fields_are_not_converted() -> None:
    """n、status 这类元数据不是金额，不能被换算成占收入比例。"""
    payload = {
        "metrics": [
            {
                "key": "expense.total",
                "value": 2413.0,
                "n": 5,
                "value_kind": "money",
                "unit": "元",
            },
            {"key": "income.total", "value": 13000.0, "value_kind": "money", "unit": "元"},
        ]
    }
    claim = render_claim("样本 [[fact:metrics.expense.total.n]] 条", [], payload, set())
    assert claim == "样本 5 条"


def test_repeated_units_after_placeholder_are_collapsed() -> None:
    """模型在自带单位的占位符后又写了一遍单位，不能渲染成「3 天 天」。"""
    payload = {"domain_facts": {"education": {"active_days": 3}}}
    claim = render_claim("覆盖 [[fact:domain_facts.education.active_days]] 天", [], payload, set())
    assert claim == "覆盖 3 天"


def test_ratio_metric_defaults_to_percent() -> None:
    """比值类指标（非必要支出占比、储蓄率等）按百分比渲染，不能退化成普通数字。"""
    payload = {
        "metrics": [
            {
                "key": "expense.nonessential_ratio",
                "value": 0.2,
                "value_kind": "ratio",
                "unit": "比例",
            }
        ]
    }
    output = {
        "highlights": [
            {
                "claim": "非必要支出占 [[fact:expense.nonessential_ratio]]",
                "figures": [{"ref": "expense.nonessential_ratio"}],
                "evidence_refs": ["expense.nonessential_ratio"],
                "strength": "medium",
                "alternatives": ["基数较小"],
            }
        ]
    }
    rendered = render_output(output, payload)
    assert "20.0%" in rendered["highlights"][0]["rendered_claim"]


def test_experiment_text_placeholders_are_rendered() -> None:
    """小实验没有 claim，但 action/success_criteria 里的占位符也必须渲染。"""
    output = {
        "experiments": [
            {
                "action": "把睡眠拉到 [[fact:health.sleep.mean_hours]]",
                "window_days": 7,
                "success_criteria": "连续一周达到 [[fact:health.sleep.mean_hours]]",
            }
        ]
    }
    rendered = render_output(output, PAYLOAD)
    experiment = rendered["experiments"][0]
    assert "[[" not in experiment["action"]
    assert "[[" not in experiment["success_criteria"]
    assert "7.5 小时" in experiment["action"]


def test_metric_meta_fields_render_as_plain_numbers() -> None:
    """金额指标的 .n 是样本量，不能跟着 value_kind 渲染成「200.0%」。"""
    payload = {
        "metrics": [
            {
                "key": "expense.total",
                "value": 0.2,
                "value_kind": "money",
                "n": 2,
                "unit": "元",
                "lineage": {"corrected": False},
            }
        ]
    }
    claim = render_claim("支出样本只有 [[fact:metrics.expense.total.n]] 条", [], payload, set())
    assert claim == "支出样本只有 2 条"


def test_domain_fact_day_and_duration_fields_carry_units() -> None:
    """domain_facts 的 active_days / minutes_total 自带单位，渲染后不必再手写。"""
    payload = {
        "domain_facts": {
            "career": {"active_days": 2, "minutes_total": 540},
            "relation": {"entries": 3},
        }
    }
    claim = render_claim(
        "活跃 [[fact:domain_facts.career.active_days]]，"
        "总时长 [[fact:domain_facts.career.minutes_total]]，"
        "关系 [[fact:domain_facts.relation.entries]] 条",
        [],
        payload,
        set(),
    )
    assert "2 天" in claim
    assert "540 分钟" in claim
    assert "3 条" in claim


def test_string_list_placeholders_are_rendered_and_validated() -> None:
    """uncertainty / data_gaps 这类字符串数组同样会写占位符，必须渲染并校验。"""
    output = {
        "highlights": [_finding()],
        "uncertainty": ["互惠均值 [[fact:health.sleep.mean_hours]] 口径不明"],
    }
    rendered = render_output(output, PAYLOAD)
    assert "[[" not in rendered["uncertainty"][0]
    assert "7.5 小时" in rendered["uncertainty"][0]

    bad = {"highlights": [_finding()], "uncertainty": ["[[fact:relation.entries]]"]}
    result = validate_output(bad, payload=PAYLOAD, min_n_by_key=MIN_N)
    assert 2 in _rules(result)


def test_resolve_ref_supports_paths_and_codes() -> None:
    assert resolve_ref(PAYLOAD, "plans.plan_A.diagnosis.verdict").value == "keep"
    assert resolve_ref(PAYLOAD, "insights.ins_01.rho").value == 0.5
    assert resolve_ref(PAYLOAD, "metrics.expense.total.value").value == 0.2
    assert resolve_ref(PAYLOAD, "nope") is None


def test_plan_codes_are_replaced_with_human_titles() -> None:
    """plan_A 这类代号只给程序与校验器用，渲染时要换成计划标题（不出英文代号）。"""
    payload = {
        "plans": [{"ref": "plan_A", "title": "早睡", "diagnosis": {"verdict": "keep"}}],
    }
    claim = render_claim("计划 plan_A 本周完成率偏低", [], payload, set())
    assert "plan_A" not in claim
    assert "早睡" in claim


def test_legacy_reports_stay_human_readable() -> None:
    """旧报告没有计划标题、也可能引用了不存在的事实字段，都不能把技术标识透给用户。"""
    claim = render_claim("计划 plan_B 的完成度 [[fact:metrics.nope.value]]", [], {}, set())
    assert "plan_B" not in claim
    assert "计划 B" in claim
    assert "[[" not in claim


def test_alternatives_inside_a_finding_are_rendered() -> None:
    """带 claim 的条目里，alternatives 等兄弟字段同样要渲染占位符。"""
    output = {
        "anomaly_impact": [
            {"claim": "本周没有异常", "alternatives": ["[[fact:metrics.nope.value]]"]}
        ]
    }
    rendered = render_output(output, {})
    assert "[[" not in rendered["anomaly_impact"][0]["alternatives"][0]


def test_render_keeps_machine_refs_untouched() -> None:
    """evidence_refs 与 figures 是给校验器、界面反查用的键，不能被本地化成文案。

    plan_A 一旦被换成「计划「早睡」」，界面就再也解析不出这条依据，
    校验器也无法回查指标。
    """
    payload = {"plans": [{"ref": "plan_A", "title": "早睡", "completion_rate": 0.5}]}
    output = {
        "findings": [
            {
                "claim": "计划 plan_A 的完成率是 [[fact:plans.plan_A.completion_rate]]",
                "figures": [{"ref": "plans.plan_A.completion_rate", "format": "percent"}],
                "evidence_refs": ["plans.plan_A.completion_rate"],
                "strength": "medium",
            }
        ]
    }
    finding = render_output(output, payload)["findings"][0]
    assert finding["evidence_refs"] == ["plans.plan_A.completion_rate"]
    assert finding["figures"] == [
        {"ref": "plans.plan_A.completion_rate", "format": "percent"}
    ]
    assert "plan_A" not in finding["rendered_claim"]
    assert "早睡" in finding["rendered_claim"]
    assert "50.0%" in finding["rendered_claim"]


# ---------------------------------------------------------------------------
# 流水线（第 9.2 / 9.6 节）
# ---------------------------------------------------------------------------
def _analytics(vault: FastVault, **overrides) -> AnalyticsService:
    values = {
        "llm_provider": PROVIDER,
        "llm_model": MODEL,
        "llm_base_url": BASE_URL,
    }
    values.update(overrides)
    return AnalyticsService(vault.conn, AppConfig(**values))


DOMAIN_OUTPUT = {
    # domain v8：领域小结只有总结 + 评价，不再写建议与缺口。
    "summary": "这一周的心情记录比较平稳，前后半周的起伏不大，周末的记录更细一些。",
    "evaluation": [
        {
            "claim": "心情均值是 [[fact:metrics.daily.mood.mean.value]]",
            "figures": [{"ref": "metrics.daily.mood.mean.value", "format": "decimal1"}],
            "evidence_refs": ["metrics.daily.mood.mean"],
            "strength": "medium",
            "causal_claim": False,
            "alternatives": ["也可能只是这一周恰好比较轻松"],
        }
    ],
}

INTEGRATION_OUTPUT = {
    "headline": (
        "这段时间的心情均值是 [[fact:metrics.daily.mood.mean.value]]，整体比较平稳。"
        "这一周记录比较连续，周末稍松一些。"
    ),
    "cross_domain": [
        {
            "claim": "这一周的心情均值是 [[fact:metrics.daily.mood.mean.value]]，整体偏稳。",
            "figures": [{"ref": "metrics.daily.mood.mean.value", "format": "decimal1"}],
            "evidence_refs": ["metrics.daily.mood.mean"],
            "strength": "medium",
            "causal_claim": False,
        }
    ],
    "experiments": [
        {"action": "固定睡前时间", "window_days": 7, "success_criteria": "入睡时间波动减小"}
    ],
}


def _chat_body(content: dict) -> dict:
    return {
        "model": MODEL,
        "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 800, "completion_tokens": 400},
    }


def _transport(content: dict) -> httpx.MockTransport:
    """所有请求都回同一份内容（用于「两次都不合规」这类降级用例）。"""
    return httpx.MockTransport(lambda _request: httpx.Response(200, json=_chat_body(content)))


def _routed_transport(
    *, domain: dict | None = None, integration: dict | None = None
) -> httpx.MockTransport:
    """按提示词区分领域 agent 与整合调用，分别回各自的假输出。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.content.decode("utf-8")
        content = domain if "【领域解读任务】" in raw else integration
        assert content is not None, "测试未覆盖的调用类型"
        return httpx.Response(200, json=_chat_body(content))

    return httpx.MockTransport(handler)


class _NoMetrics:
    """没有指标注册表的最小替身：紧凑事实只从 entry_texts 判断有没有内容。"""

    def all(self):
        return []


def _entry_facts(count: int) -> dict:
    return {
        "facts_version": 3,
        "period": {"kind": "weekly", "start": "2026-10-12", "end": "2026-10-18"},
        "profile": {"age_band": "adult", "locale": "zh-CN"},
        "entry_texts": {
            "total": count,
            "included": count,
            "items": [
                {
                    "at": "2026-10-12T09:00",
                    "domain": "daily",
                    "title": f"记录{index}",
                    "note": "",
                }
                for index in range(count)
            ],
        },
    }


def test_compact_domain_facts_carries_entry_texts_only_when_extended():
    """api_extended 才把本领域标题/备注递给模型；只出站汇总档保持纯聚合。"""
    whitelist = load_whitelist("weekly_report", AppConfig().egress_dir)
    facts = _entry_facts(3)
    source = compact_domain_facts(
        facts, "daily", _NoMetrics(), whitelist, include_entry_texts=True
    )
    assert source is not None
    assert source["entry_texts"]["total"] == 3
    assert [item["title"] for item in source["entry_texts"]["items"]] == [
        "记录0",
        "记录1",
        "记录2",
    ]
    assert (
        compact_domain_facts(
            facts, "daily", _NoMetrics(), whitelist, include_entry_texts=False
        )
        is None
    )


def test_compact_domain_facts_samples_evenly_and_filters_domain():
    """单领域超过上限时等距抽样，保留头尾；其他领域的条目不会混进来。"""
    whitelist = load_whitelist("weekly_report", AppConfig().egress_dir)
    facts = _entry_facts(200)
    facts["entry_texts"]["items"].append(
        {
            "at": "2026-10-18T09:00",
            "domain": "health",
            "title": "别的领域",
            "note": "",
        }
    )
    source = compact_domain_facts(
        facts, "daily", _NoMetrics(), whitelist, include_entry_texts=True
    )
    block = source["entry_texts"]
    assert block["total"] == 200
    assert block["included"] == _MAX_ENTRY_TEXTS_PER_DOMAIN
    assert block["items"][0]["title"] == "记录0"
    assert block["items"][-1]["title"] == "记录199"
    assert all(item["domain"] == "daily" for item in block["items"])


def test_source_digest_keeps_only_summary_and_evaluation():
    """上层报告只读下层报告的「总结 + 评价」：不重读事实，也不复述下层建议。"""
    rows = [
        {
            "period_start": "2026-10-12",
            "output": {
                "headline": "这一周状态平稳。",
                "cross_domain": [
                    {"claim": "心情稳", "rendered_claim": "心情 4 分，比较平稳"},
                    {"claim": "睡眠够"},
                ],
                "experiments": [{"action": "固定睡前时间", "window_days": 7}],
                "advice": ["不该被带进上层输入"],
            },
        },
        {
            "period_start": "2026-09-07",
            "output": {"headline": "x" * 700, "cross_domain": []},
        },
    ]
    digest = source_digest("weekly", rows)
    assert [item["ref"] for item in digest] == ["2026-10-12", "2026-09-07"]
    assert digest[0]["summary"] == "这一周状态平稳。"
    # 优先用渲染后的文字（数字已经由程序填好），没有渲染就退回 claim
    assert digest[0]["evaluation"] == ["心情 4 分，比较平稳", "睡眠够"]
    # 总结截断到 600 字，避免上层输入随周数线性膨胀
    assert len(digest[1]["summary"]) == 600
    # 建议不出现在上层输入里（上层自己写建议）
    assert "不该被带进上层输入" not in json.dumps(digest, ensure_ascii=False)


def test_source_digest_ref_is_month_for_monthly_sources():
    """年报读月报：ref 用年月（2026-10），与提示词里写的引用格式一致。"""
    digest = source_digest("monthly", [{"period_start": "2026-10-01", "output": {}}])
    assert digest[0]["ref"] == "2026-10"


def test_source_issues_rejects_unknown_refs_and_invented_numbers():
    """上层评价只能引用来源 ref，数字只能来自来源文本。"""
    payload = {
        "sources": [
            {
                "ref": "2026-10-12",
                "summary": "这一周心情均值 4 分，记录六天。",
                "evaluation": ["记录比上周更连续"],
            }
        ]
    }
    good = {
        "headline": "本月记录更连续，心情保持在 4 分附近。",
        "evaluation": [
            {
                "claim": "心情保持在 4 分附近",
                "evidence_refs": ["2026-10-12"],
                "strength": "medium",
            }
        ],
        "advice": [
            {"action": "固定记录时间", "window_days": 7, "success_criteria": "每天都有记录"}
        ],
    }
    assert _source_issues(good, payload, "adult", "zh-CN") == []

    bad_ref = {
        **good,
        "evaluation": [
            {"claim": "心情保持稳定", "evidence_refs": ["2026-09-01"], "strength": "medium"}
        ],
    }
    issues = _source_issues(bad_ref, payload, "adult", "zh-CN")
    assert any("2026-09-01" in item["message"] for item in issues)

    invented = {
        **good,
        "headline": "本月心情均值 7 分，记录二十天。",
    }
    issues = _source_issues(invented, payload, "adult", "zh-CN")
    assert any(item["path"] == "headline" for item in issues)


def test_source_issues_requires_three_blocks():
    """总结、评价、建议缺一块就不收：上层报告必须写全三块。"""
    payload = {"sources": [{"ref": "2026-10", "summary": "这个月整体平稳。", "evaluation": []}]}
    issues = _source_issues({"headline": ""}, payload, "adult", "zh-CN")
    paths = {item["path"] for item in issues}
    assert {"headline", "evaluation", "advice"} <= paths


def test_integration_facts_carries_summary_and_findings():
    """整合调用拿到的每条领域小结只有总结 + 评价；降级领域不把本地数字回喂模型。"""
    rows = [
        {
            "domain": "daily",
            "summary": "这周前两天状态不错。",
            "findings": [{"claim": "心情比上期稳"}],
            "degraded": False,
        },
        {
            "domain": "health",
            "summary": "本地兜底文字",
            "findings": [],
            "degraded": True,
        },
    ]
    payload = integration_facts({"facts_version": 4}, rows)
    assert payload["domain_summaries"] == [
        {
            "domain": "daily",
            "summary": "这周前两天状态不错。",
            "findings": [{"claim": "心情比上期稳"}],
        }
    ]


def test_integration_facts_carries_domain_facts():
    """整合输入要带定性领域的过程统计，领域小结里的占位符才解析得开。"""
    facts = {
        "facts_version": 4,
        "domain_facts_version": 2,
        "domain_facts": {"goal": {"goals_total": 2, "plans_done": 0}},
    }
    payload = integration_facts(facts, [])
    assert payload["domain_facts_version"] == 2
    assert payload["domain_facts"] == {"goal": {"goals_total": 2, "plans_done": 0}}


def test_reports_in_period_keeps_one_row_per_period(fast_vault: FastVault) -> None:
    """同一周被重跑过时只留最新一份；降级报告与区间外的报告都不作为上层输入。"""
    def _save(period_start: str, period_end: str, headline: str, status: str) -> None:
        save_report(
            fast_vault.conn,
            kind="weekly",
            period_start=period_start,
            period_end=period_end,
            status=status,
            facts={},
            summary_md="",
            output={"headline": headline},
        )

    _save("2026-10-12", "2026-10-18", "第一版", "ok")
    _save("2026-10-12", "2026-10-18", "第二版", "ok")
    _save("2026-10-19", "2026-10-25", "降级的一周", "degraded")
    _save("2026-09-07", "2026-09-13", "上个月的一周", "ok")

    rows = reports_in_period(
        fast_vault.conn, kind="weekly", start="2026-10-01", end="2026-10-31"
    )
    assert len(rows) == 1
    assert rows[0]["period_start"] == "2026-10-12"
    assert rows[0]["output"]["headline"] in ("第一版", "第二版")


def test_truncated_integration_degrades_with_clear_reason(
    fast_vault: FastVault, monkeypatch
) -> None:
    """预算被推理吃掉、正文截断时，降级原因要写清「被截断」，而不是笼统的校验失败。"""
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(fast_vault.conn, "api_summary")
    analytics = _analytics(fast_vault)
    body = {
        "model": MODEL,
        "choices": [{"message": {"content": '{"headline": "'}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 800, "completion_tokens": 8000},
    }
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=body))
    report = generate_report(analytics, kind="weekly", end="2026-10-18", transport=transport)
    assert report["status"] == "degraded"
    issues = report["output"]["reason"]["params"]["issues"]
    assert any("截断" in item["message"] for item in issues)


def _seed_daily_week(vault: FastVault) -> None:
    """给 2026-10-12 ~ 10-18 这一周补满 daily 记录，让 daily 领域有数据可分析。"""
    for offset in range(7):
        day = (date(2026, 10, 12) + timedelta(days=offset)).isoformat()
        vault.service.entries.create(
            {
                "domain": "daily",
                "occurred_at": f"{day}T12:00:00+08:00",
                "detail": {"mood": 4},
            }
        )


def test_pipeline_generates_ok_report(fast_vault: FastVault, monkeypatch) -> None:
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(fast_vault.conn, "api_summary")
    _seed_daily_week(fast_vault)
    analytics = _analytics(fast_vault)

    report = generate_report(
        analytics,
        kind="weekly",
        end="2026-10-18",
        transport=_routed_transport(domain=DOMAIN_OUTPUT, integration=INTEGRATION_OUTPUT),
    )
    assert report["status"] == "ok"
    assert report["model"] == MODEL
    assert report["template_hash"] and report["snapshot_hash"]
    # 溯源：提示词版本与指标注册表版本/指纹一并落库（NFR-06）
    assert report["template_version"] == 13  # 周报用自己的提示词 weekly.v13
    assert report["metrics_version"]
    assert len(report["metrics_hash"]) == 64

    output = report["output"]
    # v12 收敛成三块：总结（headline）/ 评价（cross_domain）/ 建议（experiments）
    assert {"cross_domain", "experiments"} <= set(output)
    assert "data_gaps" not in output and "uncertainty" not in output
    # 每个有数据的领域一段小结；空领域只记名字，不调用模型
    assert [row["domain"] for row in output["domains"]] == ["daily"]
    assert output["domains"][0]["degraded"] is False
    assert "daily" not in output["no_data_domains"]
    assert "dream" in output["no_data_domains"]
    # 整合调用也拿得到紧凑指标，概览里的占位符能继续解析成程序算出的值
    assert "[[" not in output["headline"]
    assert "4 分" in output["headline"]

    # 结论仍可追溯：evidence_refs 都能解析，占位符已换成程序算出的值
    finding = output["domains"][0]["findings"][0]
    for ref in finding["evidence_refs"]:
        assert resolve_ref(report["facts"], ref) is not None
    assert "[[fact:" not in finding["rendered_claim"]

    # 领域小结只有总结 + 评价：建议只在整份报告这一层
    assert "advice" not in output["domains"][0]
    assert "gaps" not in output["domains"][0]
    assert output["experiments"][0]["action"] == "固定睡前时间"
    assert "固定睡前时间" in report["summary_md"]

    # 成本可见：一次领域调用 + 一次整合调用，token 与费用随报告落库
    usage = output["ai_usage"]
    assert usage["calls"] == 2
    assert usage["prompt_tokens"] == 1600
    assert usage["completion_tokens"] == 800
    assert usage["cost_est"] > 0
    assert usage["degraded_domains"] == []

    # 两次出站都回填为 sent
    rows = [dict(row) for row in fast_vault.conn.query("SELECT * FROM egress_log")]
    assert len(rows) == 2 and all(row["status"] == "sent" for row in rows)


def test_domain_failure_only_degrades_that_domain(fast_vault: FastVault, monkeypatch) -> None:
    """领域小结不合规时用本地文字兜底，整份报告仍然成立，整合照常调用。"""
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(fast_vault.conn, "api_summary")
    _seed_daily_week(fast_vault)
    analytics = _analytics(fast_vault)

    bad_domain = {"summary": "短", "findings": [], "gaps": []}
    report = generate_report(
        analytics,
        kind="weekly",
        end="2026-10-18",
        transport=_routed_transport(domain=bad_domain, integration=INTEGRATION_OUTPUT),
    )
    assert report["status"] == "ok"
    row = report["output"]["domains"][0]
    assert row["domain"] == "daily" and row["degraded"] is True
    assert report["output"]["ai_usage"]["degraded_domains"] == ["daily"]


def test_bad_summary_keeps_validated_findings(fast_vault: FastVault, monkeypatch) -> None:
    """小结写了手写数字时只把小结点退回本地口径，逐条校验过的评价仍要保留。

    一个领域不该因为一句话不合规整段作废：findings 已经逐条校验，
    丢掉它们等于白花一次调用。
    """
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(fast_vault.conn, "api_summary")
    _seed_daily_week(fast_vault)
    analytics = _analytics(fast_vault)

    partial = {
        # 手写数字 → summary 不合规，但下面的评价都是合规的
        "summary": "这周记录了 3 天心情，整体平稳。",
        "evaluation": DOMAIN_OUTPUT["evaluation"],
        # 即使模型多写了建议，领域层也不收：建议只由周报这一层给
        "advice": ["把睡前的准备提前半小时"],
    }
    report = generate_report(
        analytics,
        kind="weekly",
        end="2026-10-18",
        transport=_routed_transport(domain=partial, integration=INTEGRATION_OUTPUT),
    )
    row = report["output"]["domains"][0]
    assert row["degraded"] is True
    assert row["summary"] != partial["summary"]
    assert row["findings"] and row["findings"][0]["rendered_claim"]
    assert "advice" not in row and "gaps" not in row


MONTHLY_OUTPUT = {
    # monthly v12：只读本月各份周报的总结与评价，一次调用产出三块。
    "headline": (
        "这个月的周报记录集中在日常与健康，后几周的记录比前面更连续，"
        "整月的节奏比前一段更稳。"
    ),
    "evaluation": [
        {
            "claim": "后几周的记录比前面更连续",
            "evidence_refs": ["2026-10-12"],
            "strength": "medium",
            "causal_claim": False,
        }
    ],
    "advice": [
        {
            "action": "下月把记录时间固定在晚上",
            "window_days": 30,
            "success_criteria": "每周都有完整的记录",
        }
    ],
}

YEARLY_OUTPUT = {
    # yearly v6：只读本年各份月报的总结与评价，ref 用年月。
    "headline": "这一年记录习惯在后半年逐步稳定下来，有月报的月份里记录连续性在提升。",
    "evaluation": [
        {
            "claim": "记录连续性在后半年更好",
            "evidence_refs": ["2026-10"],
            "strength": "medium",
            "causal_claim": False,
        }
    ],
    "advice": [
        {
            "action": "明年把记录时间固定住",
            "window_days": 30,
            "success_criteria": "每周至少留下一次记录",
        }
    ],
}


def test_pipeline_loads_period_prompt_by_kind(fast_vault: FastVault, monkeypatch) -> None:
    """周报跑领域 agent；月报只读周报、年报只读月报，各只有一次调用。"""
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(fast_vault.conn, "api_summary")
    _seed_daily_week(fast_vault)
    analytics = _analytics(fast_vault)

    weekly = generate_report(
        analytics,
        kind="weekly",
        end="2026-10-18",
        transport=_routed_transport(domain=DOMAIN_OUTPUT, integration=INTEGRATION_OUTPUT),
    )
    assert weekly["status"] == "ok"
    assert weekly["template_version"] == 13

    monthly = generate_report(
        analytics,
        kind="monthly",
        end="2026-10-18",
        transport=_routed_transport(integration=MONTHLY_OUTPUT),
    )
    assert monthly["status"] == "ok"
    assert monthly["template_version"] == 12
    assert {"evaluation", "advice"} <= set(monthly["output"])
    # 月报不再管领域：没有分领域小节，也没有 domain_summaries 这一屏
    assert "domains" not in monthly["output"]

    yearly = generate_report(
        analytics,
        kind="yearly",
        end="2026-10-18",
        transport=_routed_transport(integration=YEARLY_OUTPUT),
    )
    assert yearly["status"] == "ok"
    assert yearly["template_version"] == 6
    assert {"evaluation", "advice"} <= set(yearly["output"])
    assert "domains" not in yearly["output"]


def test_monthly_without_weekly_reports_degrades(fast_vault: FastVault, monkeypatch) -> None:
    """本期没有周报时月报不调用模型，直接降级并写清原因（不假装覆盖整月）。"""
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(fast_vault.conn, "api_summary")
    analytics = _analytics(fast_vault)

    report = generate_report(
        analytics,
        kind="monthly",
        end="2026-10-18",
        transport=_transport(MONTHLY_OUTPUT),
    )
    assert report["status"] == "degraded"
    assert report["output"]["reason"]["key"] == "report.degrade.no_sources"
    # 没有下层报告就没有出站记录：一次调用都不该发生
    rows = list(fast_vault.conn.query("SELECT * FROM egress_log"))
    assert rows == []


def test_pipeline_degrades_without_egress(fast_vault: FastVault) -> None:
    """策略为 none（默认）时不出站，报告自动降级，不调用模型。"""
    analytics = _analytics(fast_vault)
    report = generate_report(analytics, kind="weekly", end="2026-10-18")
    assert report["status"] == "degraded"
    assert report["output"]["degraded"] is True
    assert "未经 LLM 解读" in report["summary_md"]
    row = dict(fast_vault.conn.query_one("SELECT * FROM egress_log ORDER BY ts DESC LIMIT 1"))
    assert row["status"] == "blocked"


def test_pipeline_degrades_on_validation_failure(fast_vault: FastVault, monkeypatch) -> None:
    """整合调用两次都给出不合规输出 → 降级，而不是写出一条错误的结论。"""
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(fast_vault.conn, "api_summary")
    analytics = _analytics(fast_vault)
    bad = {
        "headline": "本周睡了 7 小时",
        "cross_domain": [],
        "experiments": [],
        "data_gaps": [],
        "uncertainty": [],
    }
    report = generate_report(
        analytics, kind="weekly", end="2026-10-18", transport=_transport(bad)
    )
    assert report["status"] == "degraded"
    assert report["summary_md"]


def test_list_reports_returns_light_rows(fast_vault: FastVault) -> None:
    analytics = _analytics(fast_vault)
    generate_report(analytics, kind="weekly", end="2026-10-18")
    items = list_reports(fast_vault.conn)
    assert len(items) == 1
    assert items[0]["kind"] == "weekly"
    assert "facts" not in items[0] and "output" not in items[0]


# ---------------------------------------------------------------------------
# 儿童报告过滤（第 9.5 节、P5 W3 任务 4）
# ---------------------------------------------------------------------------
_CHILD_FACTS = {
    "metrics": [
        {
            "key": "plan.completion_rate",
            "description": "i18n:metric.plan.completion_rate",
            "unit": "%",
            "value": 0.8,
            "prev_value": 0.5,
            "delta": 0.3,
            "n": 3,
            "status": "ok",
        }
    ],
    "plans": [{"ref": "plan_A", "diagnosis": {"verdict": "retire", "reasons": []}}],
    "data_gaps": [{"key": "facts.gap.domain_empty", "params": {"domain": "dream"}}],
}


def _assert_no_banned(text: str) -> None:
    for word in (*BANNED_ALWAYS, *BANNED_CHILD):
        assert word not in text, f"儿童报告仍含禁用表述：{word}"
    assert "%" not in text and "％" not in text


def test_build_child_report_replaces_verdict_and_drops_percent() -> None:
    summary, output = build_child_report(_CHILD_FACTS)
    assert output["child"] is True and output["degraded"] is True
    # retire 的判定口气换成鼓励说法；百分号被去掉，只留「记了几次」
    assert "先放一放也没关系" in summary
    assert "一共记了 3 次" in summary
    # 成人指标名里的公式与「完成率」不得留给孩子（W3.2）
    assert "计划的完成情况" in summary
    assert "完成率" not in summary
    # 结构仍保留原始判定码，供阅读器复用
    assert output["plans"][0]["verdict"] == "retire"
    _assert_no_banned(summary)
    _assert_no_banned(output["metrics"][0]["text"])
    _assert_no_banned(output["plans"][0]["text"])


def test_child_gap_line_hides_metric_key_and_status_code() -> None:
    """数据缺口也换孩子话：不露 `daily.mood.mean` 这类键与 `insufficient_data` 状态码。"""
    facts = {
        **_CHILD_FACTS,
        "data_gaps": [
            {
                "key": "facts.gap.metric_not_ok",
                "params": {"metric": "daily.mood.mean", "status": "insufficient_data"},
            }
        ],
    }
    summary, output = build_child_report(facts)
    line = output["data_gaps"][0]
    assert "心情" in line
    assert "daily.mood.mean" not in line
    assert "insufficient_data" not in line
    _assert_no_banned(summary)
    _assert_no_banned(line)


def test_degrade_metric_line_labels_real_status_codes() -> None:
    """指标状态码是 ok/insufficient_data/not_computable：不能把缺数据的指标写成「已就绪」。"""
    facts = {
        "metrics": [
            {
                "key": "daily.mood.mean",
                "description": "i18n:metric.daily.mood.mean",
                "unit": "",
                "status": "insufficient_data",
                "value": None,
                "n": 0,
            },
            {
                "key": "daily.stress.mean",
                "description": "i18n:metric.daily.stress.mean",
                "unit": "",
                "status": "not_computable",
                "value": None,
                "n": 0,
            },
        ],
        "plans": [],
        "data_gaps": [],
    }
    summary, _ = build_degraded(facts)
    assert "数据不足" in summary
    assert "暂时算不出来" in summary
    assert "已就绪" not in summary


def test_pipeline_child_report_is_filtered(fast_vault: FastVault) -> None:
    """child 档报告永远降级并自动过儿童过滤层（第 9.5 / 9.6 节）。"""
    fast_vault.conn.execute("UPDATE profile_meta SET age_band = 'child' WHERE id = 1")
    fast_vault.service.longterm.create_plan({"title": "每天读书", "cadence": "daily"})
    analytics = _analytics(fast_vault)

    report = generate_report(analytics, kind="weekly", end="2026-10-18")
    assert report["status"] == "degraded"
    assert report["output"]["child"] is True
    assert "给你的小报告" in report["summary_md"]
    _assert_no_banned(report["summary_md"])


# ---------------------------------------------------------------------------
# 调度与补跑（第 3.5 节 B）
# ---------------------------------------------------------------------------
def test_weekly_due_end_respects_run_time() -> None:
    sunday = datetime(2026, 10, 18, 22, 0)
    assert weekly_due_end(sunday) == date(2026, 10, 18)
    before = datetime(2026, 10, 18, 20, 0)
    assert weekly_due_end(before) == date(2026, 10, 11)
    friday = datetime(2026, 10, 16, 10, 0)
    assert weekly_due_end(friday) == date(2026, 10, 11)


def test_monthly_due_end_tracks_last_day() -> None:
    assert monthly_due_end(datetime(2026, 10, 16, 10, 0)) == date(2026, 9, 30)
    assert monthly_due_end(datetime(2026, 10, 31, 22, 0)) == date(2026, 10, 31)
    assert monthly_due_end(datetime(2026, 10, 31, 10, 0)) == date(2026, 9, 30)


def test_missing_periods_dedupes_by_report(fast_vault: FastVault) -> None:
    now = datetime(2026, 10, 18, 22, 0)
    assert len(missing_periods(fast_vault.conn, now)) == 2
    analytics = _analytics(fast_vault)
    generate_report(analytics, kind="weekly", end="2026-10-18")
    left = missing_periods(fast_vault.conn, now)
    assert [item["kind"] for item in left] == ["monthly"]


# ---------------------------------------------------------------------------
# 阅读器行为：采纳小实验（第 9.7 节）
# ---------------------------------------------------------------------------
def test_adopt_experiment_creates_once_plan(fast_vault: FastVault) -> None:
    report = {
        "output": {
            "experiments": [
                {"action": "固定睡前时间", "window_days": 7, "success_criteria": "波动减小"}
            ]
        }
    }
    plan = adopt_experiment(fast_vault.service, report, 0)
    assert plan["cadence"] == "once"
    assert plan["status"] == "active"
    assert plan["expected_output"] == "波动减小"
    assert plan["end_date"] == (date.today() + timedelta(days=7)).isoformat()


def test_adopt_experiment_out_of_range(fast_vault: FastVault) -> None:
    with pytest.raises(InvalidInputError):
        adopt_experiment(fast_vault.service, {"output": {}}, 0)


# ---------------------------------------------------------------------------
# 提示词加载（第 9.4 节）
# ---------------------------------------------------------------------------
def test_load_prompt_reads_front_matter_and_hash() -> None:
    template = load_prompt("weekly", AppConfig().prompts_dir)
    assert template.template_id == "weekly"
    assert template.purpose == "weekly_report"
    assert template.version == 13  # 取版本号最大的文件（v13 重写，仍为三块）
    assert len(template.hash) == 64
    assert "你是这份个人生活报告的顾问" in template.system
    assert "{{ language }}" in template.system
    assert "{{ facts_yaml }}" in template.user
