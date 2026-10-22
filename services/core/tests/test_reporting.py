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
from lifeos.privacy import write_policy
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
from lifeos.reporting.degrade import build_degraded
from lifeos.reporting.render import render_claim
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


def test_rule4_alternatives_required() -> None:
    assert 4 in _rules(_validate(_finding(alternatives=[])))


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


def test_money_metric_defaults_to_percent_and_correction_marker() -> None:
    payload = {
        "metrics": [
            {
                "key": "expense.total",
                "value": 0.2,
                "value_kind": "money",
                "unit": "元",
                "lineage": {"corrections": [{"id": "c1"}]},
            }
        ]
    }
    output = {
        "highlights": [
            {
                "claim": "非必要支出占 [[fact:expense.total]]",
                # 不指定 format：金额类指标由程序按「占收入比例」渲染成百分比
                "figures": [{"ref": "expense.total"}],
                "evidence_refs": ["expense.total"],
                "strength": "medium",
                "alternatives": ["基数较小"],
            }
        ]
    }
    rendered = render_output(output, payload)
    claim = rendered["highlights"][0]["rendered_claim"]
    assert "20.0%" in claim  # money 指标按百分比渲染
    assert "已按你的纠正" in claim  # 被纠正过的字段追加标记


def test_ratio_metric_defaults_to_percent_after_egress_relabel() -> None:
    """出站把金额改成 ratio 后，本地渲染仍要按百分比，不能退化成普通数字。"""
    payload = {
        "metrics": [
            {
                "key": "expense.total",
                "value": 0.2,
                "value_kind": "ratio",
                "unit": "占收入比例（1 表示 100%）",
                "lineage": {"corrected": False},
            }
        ]
    }
    output = {
        "highlights": [
            {
                "claim": "非必要支出占 [[fact:expense.total]]",
                "figures": [{"ref": "expense.total"}],
                "evidence_refs": ["expense.total"],
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
    "summary": "这一周的心情记录比较平稳，起伏不大，继续按现在的节奏记录就好。",
    "findings": [
        {
            "claim": "心情均值是 [[fact:metrics.daily.mood.mean.value]]",
            "figures": [{"ref": "metrics.daily.mood.mean.value", "format": "decimal1"}],
            "evidence_refs": ["metrics.daily.mood.mean"],
            "strength": "medium",
            "causal_claim": False,
            "alternatives": ["也可能只是这一周恰好比较轻松"],
        }
    ],
    "gaps": [],
}

INTEGRATION_OUTPUT = {
    "headline": (
        "这段时间的心情均值是 [[fact:metrics.daily.mood.mean.value]]，整体比较平稳。"
        "下面先从有记录的领域说起，其余领域可以之后再慢慢补齐。"
        "接下来先保持记录习惯，再挑一两个小实验试试看。"
    ),
    "cross_domain": [],
    "experiments": [
        {"action": "固定睡前时间", "window_days": 7, "success_criteria": "入睡时间波动减小"}
    ],
    "data_gaps": [],
    "uncertainty": ["记录时点不一致"],
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
    assert report["template_version"] == 2  # 整合提示词 integration.v2
    assert report["metrics_version"]
    assert len(report["metrics_hash"]) == 64

    output = report["output"]
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
    assert template.version == 9  # 取版本号最大的文件（v9 概览扩写）
    assert len(template.hash) == 64
    assert "你是一个个人数据分析助手" in template.system
    assert "{{ language }}" in template.system
    assert "{{ facts_yaml }}" in template.user
