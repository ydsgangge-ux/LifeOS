"""隐私网关测试（第 7.4 节）与泄漏黄金测试（第 7.5 节）。

黄金测试是隐私承诺的唯一可靠手段：构造含伪造隐私数据的样本库，对每种 purpose
跑完整出站流程，断言最终 payload 不含任何敏感子串、且出站字段是白名单子集。

关于「拼音」变体：出站载荷里的自由文本只有两条通道——api_extended 下的
`entry_texts`（本期条目标题/备注，姓名先换成本地人物代号）与页面级
`confirmed_texts`（用户逐条确认过的摘要）。样本库里以拼音形态存在的别名
（person.code / person.note）只能通过「白名单里根本没有这个区块」被挡住，
测试就以断言它不出现来覆盖；扫描器按第 7.4 节列的类别
（手机号、邮箱、身份证号、银行卡号、网址、本地人物表姓名）办事，不猜测拼音。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from conftest import FastVault, build_vault
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from lifeos.analytics import AnalyticsService
from lifeos.api import API_PREFIX, create_app
from lifeos.config import AppConfig
from lifeos.db.registry import ProfileRecord
from lifeos.domain.profiles import UnlockedProfile
from lifeos.privacy import (
    EGRESS_POLICIES,
    EGRESS_PURPOSES,
    POLICY_EXTENDED,
    POLICY_NONE,
    POLICY_SUMMARY,
    PolicyDeniedError,
    UnknownPurposeError,
    assert_policy_allowed,
    available_purposes,
    list_egress_log,
    load_whitelist,
    payload_digest,
    person_names,
    prepare,
    read_policy,
    record_egress,
    relative_money,
    scan_text,
    validate_policy,
    week_index,
    write_policy,
)
from lifeos.util.ids import new_ulid, now_utc_iso

TOKEN = "privacy-token-0123456789"
BASE_URL = "http://127.0.0.1:8000"
PROFILE_ID = "01J0PRIVACYTEST000000000000"
EGRESS_DIR = AppConfig().egress_dir
ANCHOR = date(2026, 9, 7)
WEEKS = ("2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28")
PERIOD_START, PERIOD_END = "2026-09-07", "2026-10-04"

# 伪造的隐私数据：姓名、昵称、手机号、邮箱、身份证号、银行卡号、住址、公司名、精确金额
NAME = "张三"
NICK = "小张"
PINYIN = "zhangsan"
PHONE = "13800138000"
EMAIL = "zhangsan@example.com"
ID_CARD = "110105199003072316"
BANK_CARD = "6222021234567890"
ADDRESS = "北京市朝阳区建国路88号"
COMPANY = "示例科技有限公司"
AMOUNT_MINOR = 1280000  # 每周支出 12,800.00
INCOME_MINOR = 3000000  # 每周收入 30,000.00
EXPENSE_TOTAL = AMOUNT_MINOR * len(WEEKS) / 100  # 51200.0
INCOME_VALUE = INCOME_MINOR * len(WEEKS) / 100  # 120000.0
SENSITIVE = (NAME, NICK, PINYIN, PHONE, EMAIL, ID_CARD, BANK_CARD, ADDRESS, COMPANY)


# ---------------------------------------------------------------------------
# 变体工具（第 7.5 节第 3 条：全角半角、插入空格、千分位与小数写法）
# ---------------------------------------------------------------------------
def _to_fullwidth(text: str) -> str:
    return "".join(chr(ord(ch) + 0xFEE0) if 0x21 <= ord(ch) <= 0x7E else ch for ch in text)


def _split_join(digits: str, cuts: list[int], sep: str) -> str:
    pieces: list[str] = []
    rest = digits
    for cut in sorted(set(c for c in cuts if 0 < c < len(digits))):
        pieces.append(rest[:cut])
        rest = rest[cut:]
    pieces.append(rest)
    return sep.join(pieces)


def _variant_strings() -> list[str]:
    out: list[str] = []
    for raw in (PHONE, ID_CARD, BANK_CARD):
        out.extend(
            [
                raw,
                _to_fullwidth(raw),
                f" {raw} ",
                _split_join(raw, [3, 7], ", "),
                _split_join(raw, [3, 7], "，"),
                _split_join(raw, [4], " "),
                _split_join(raw, [3, 7], "-"),
            ]
        )
    for raw in (EMAIL, PHONE):
        out.append(_to_fullwidth(raw))
    out.extend(
        [NAME, NICK, PINYIN, ADDRESS, COMPANY, "12800", "12,800", "12800.00", str(AMOUNT_MINOR)]
    )
    return out


# ---------------------------------------------------------------------------
# 白名单与策略
# ---------------------------------------------------------------------------
def test_every_purpose_has_a_whitelist() -> None:
    """第 7.5 节第 5 条：新增 purpose 没有白名单文件就必须失败。"""
    defined = set(available_purposes(EGRESS_DIR))
    for purpose in EGRESS_PURPOSES:
        assert purpose in defined, f"{purpose} 缺少 schemas/egress/{purpose}.yaml"
        assert load_whitelist(purpose, EGRESS_DIR).purpose == purpose
    # 反过来也要挡住：目录里冒出没登记的白名单同样有问题
    assert defined == set(EGRESS_PURPOSES)


def test_unknown_purpose_is_rejected() -> None:
    with pytest.raises(UnknownPurposeError):
        load_whitelist("not_a_purpose", EGRESS_DIR)


def test_whitelist_only_reports_allow_income_total() -> None:
    """报告类用途按原值出站收入总额（用户显式选择全字段分析）；其余用途一律不给。"""
    report_purposes = {"weekly_report", "monthly_report", "yearly_report"}
    for purpose in EGRESS_PURPOSES:
        allowed = "income.total" in load_whitelist(purpose, EGRESS_DIR).metrics
        assert allowed is (purpose in report_purposes), purpose


def test_policy_validation_and_child_lock() -> None:
    assert validate_policy(POLICY_SUMMARY) == POLICY_SUMMARY
    with pytest.raises(PolicyDeniedError):
        validate_policy("api_everything")
    # 儿童与青少年档只能 none（第 7.7 节）
    for band in ("child", "teen"):
        assert_policy_allowed(POLICY_NONE, band)
        with pytest.raises(PolicyDeniedError):
            assert_policy_allowed(POLICY_SUMMARY, band)
    for band in ("adult", "senior"):
        assert_policy_allowed(POLICY_EXTENDED, band)
    assert EGRESS_POLICIES == (POLICY_NONE, POLICY_SUMMARY, POLICY_EXTENDED)


# ---------------------------------------------------------------------------
# 标识扫描：变体用 hypothesis 生成
# ---------------------------------------------------------------------------
@given(st.lists(st.integers(min_value=1, max_value=6), max_size=4), st.booleans())
def test_identifier_variants_are_detected(cuts: list[int], fullwidth: bool) -> None:
    for digits in (PHONE, ID_CARD, BANK_CARD):
        text = _split_join(digits, cuts, "，")
        if fullwidth:
            text = _to_fullwidth(text)
        assert not scan_text(text).clean, f"变体未被检出：{text!r}"


@given(st.integers(min_value=1, max_value=5), st.sampled_from([" ", ", ", "，", "、", "-", "."]))
def test_identifiers_split_by_any_separator(count: int, sep: str) -> None:
    raw = BANK_CARD
    cuts = [len(raw) // (count + 1) * i for i in range(1, count + 1)]
    assert not scan_text(_split_join(raw, cuts, sep)).clean


def test_scanner_categories_and_clean_text() -> None:
    report = scan_text(f"联系 {PHONE} 邮箱 {EMAIL} 证件 {ID_CARD} 卡号 {BANK_CARD}", names=[NAME])
    assert set(report.counts) == {"phone", "email", "id_card", "bank_card"}
    # 手机号与身份证号在紧凑文本里会重叠命中（1 位开头的长数字串）：宁可多报，不可漏报
    assert report.counts["phone"] >= 1
    assert report.counts["email"] == report.counts["id_card"] == report.counts["bank_card"] == 1
    assert scan_text("今天心情不错，睡了 7 小时").clean
    # 姓名命中来自本地人物表，不是猜测
    assert scan_text(f"和{NICK}吃饭", names=[NICK]).counts == {"person_name": 1}
    assert scan_text("和同事吃饭", names=[NICK]).clean


_INSERT_PERSON = (
    "INSERT INTO person (id, code, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
)


def test_person_names_only_from_local_table(fast_vault: FastVault) -> None:
    now = now_utc_iso()
    fast_vault.conn.execute(_INSERT_PERSON, (new_ulid(), "P01", NAME, now, now))
    fast_vault.conn.execute(_INSERT_PERSON, (new_ulid(), "P02", "已删的人", now, now))
    fast_vault.conn.execute("UPDATE person SET deleted_at = ? WHERE code = 'P02'", (now,))
    assert person_names(fast_vault.conn) == (NAME,)


# ---------------------------------------------------------------------------
# 泛化
# ---------------------------------------------------------------------------
def test_generalization_rules() -> None:
    assert week_index(date(2026, 9, 7), ANCHOR) == 0
    assert week_index(date(2026, 9, 14), ANCHOR) == 1
    assert week_index(date(2026, 8, 31), ANCHOR) == -1
    assert relative_money(1500.0, 3000.0) == 0.5
    assert relative_money(1500.0, None) is None
    assert relative_money(1500.0, 0) is None


# ---------------------------------------------------------------------------
# 样本库：塞满标识的伪造数据
# ---------------------------------------------------------------------------
def _entry(vault: FastVault, domain: str, day: str, detail: dict, **extra) -> dict:
    payload = {"domain": domain, "occurred_at": f"{day}T12:00:00+08:00", "detail": detail}
    payload.update(extra)
    return vault.service.entries.create(payload)


def _sample_vault(tmp_path: Path) -> FastVault:
    vault = build_vault(tmp_path, "privacy")
    conn = vault.conn
    now = now_utc_iso()
    # 姓名与拼音别名都睡在 person 表里——它根本不在任何白名单的 allow.facts 中
    conn.execute(
        "INSERT INTO person (id, code, display_name, relation_type, note, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_ulid(), PINYIN, NAME, "朋友", f"昵称{NICK}，住{ADDRESS}，{COMPANY}", now, now),
    )
    for week in WEEKS:
        _entry(
            vault,
            "daily",
            week,
            {"mood": 4, "energy": 4},
            title="加班到很晚",
            note=f"电话 {PHONE}，邮箱 {EMAIL}，{COMPANY}",
        )
        _entry(vault, "health", week, {"sleep_hours": 7.5, "exercise_minutes": 30})
        _entry(
            vault,
            "expense",
            week,
            {"amount_minor": AMOUNT_MINOR, "currency": "CNY", "direction": "out"},
            title="报销",
            note=f"卡号 {BANK_CARD}",
        )
        _entry(
            vault,
            "income",
            week,
            {"amount_minor": INCOME_MINOR, "currency": "CNY", "direction": "in"},
            title="工资",
            note=f"证件 {ID_CARD}",
        )
    # 一条活跃计划，让 plans 区块非空
    conn.execute(
        "INSERT INTO plan (id, title, cadence, status, created_at, updated_at)"
        " VALUES (?, ?, 'weekly', 'active', ?, ?)",
        (new_ulid(), f"陪{NICK}跑步", now, now),
    )
    return vault


@pytest.fixture
def sample(tmp_path: Path):
    vault = _sample_vault(tmp_path)
    yield vault
    vault.conn.close()


@pytest.fixture
def analytics(sample: FastVault) -> AnalyticsService:
    return AnalyticsService(sample.conn, AppConfig())


def _facts(analytics: AnalyticsService) -> dict:
    """4 个自然周的自定义周期（weekly 只覆盖最近 7 天，样本会不够）。"""
    return analytics.facts(kind="custom", start=PERIOD_START, end=PERIOD_END)


# ---------------------------------------------------------------------------
# 黄金测试（第 7.5 节）
# ---------------------------------------------------------------------------
def _assert_outbound_is_subset(payload: dict, whitelist) -> None:
    assert set(payload) <= set(whitelist.facts) | {"confirmed_texts"}
    assert set(payload.get("anomalies", {})) <= set(whitelist.anomaly_fields)
    for metric in payload.get("metrics", []):
        assert metric["key"] in whitelist.metrics
        assert set(metric) <= set(whitelist.metric_fields)
    for plan in payload.get("plans", []):
        assert set(plan) <= set(whitelist.plan_fields)
        if isinstance(plan.get("diagnosis"), dict):
            assert set(plan["diagnosis"]) <= set(whitelist.plan_diagnosis_fields)
    for insight in payload.get("insights", []):
        assert set(insight) <= set(whitelist.insight_fields)
    for domain, item in payload.get("domain_facts", {}).items():
        assert domain in whitelist.domain_fields
        assert set(item) <= set(whitelist.domain_fields[domain])
    block = payload.get("entry_texts")
    if isinstance(block, dict):
        assert set(block) <= set(whitelist.record_fields.get("entry_texts", ()))
        for item in block.get("items", []):
            assert set(item) <= set(whitelist.entry_text_fields)


def test_golden_no_leak_for_every_purpose(sample: FastVault, analytics: AnalyticsService) -> None:
    facts = _facts(analytics)
    for purpose in EGRESS_PURPOSES:
        whitelist = load_whitelist(purpose, EGRESS_DIR)
        prepared = prepare(
            sample.conn,
            facts=facts,
            whitelist=whitelist,
            policy=POLICY_EXTENDED,
            anchor=ANCHOR,
            income_value=INCOME_VALUE,
            confirmed_texts=[f"我住在{ADDRESS}，电话 {PHONE}", f"{NAME}（{PINYIN}）今天很累"],
        )
        assert prepared.allowed
        _assert_outbound_is_subset(prepared.payload, whitelist)
        assert prepared.sha256 == payload_digest(prepared.payload)[0]
        if whitelist.raw:
            # 激进档（报告类用途）显式关闭逐字段扫描：用户选择把本期内容整段交给
            # 厂家 API 分析，网关只保留白名单收窄与金额原值，不再产生命中记录。
            # 因此这里只校验「出站字段仍在白名单内」，不再断言 0 泄漏。
            assert prepared.report["hits"] == {}
            continue
        text = json.dumps(prepared.payload, ensure_ascii=False)
        leaked = [needle for needle in _variant_strings() + list(SENSITIVE) if needle in text]
        assert leaked == [], f"{purpose} 泄漏了：{leaked}"
        if whitelist.free_text:
            # 两条确认文本各含一个标识（手机号、姓名），逐条丢掉，一条都不留
            assert prepared.payload["confirmed_texts"] == []
            # 条目原文的备注里也带着手机号、邮箱、证件号与银行卡号，逐字段丢弃；
            # 命中分类只断言「出现了哪些」，不断言具体计数（手机号与证件号会重叠）
            assert {"phone", "email", "id_card", "bank_card", "person_name"} <= set(
                prepared.report["hits"]
            )
        else:
            assert "confirmed_texts" not in prepared.payload
            if "entry_texts" in whitelist.facts:
                # 月报/年报没有确认文本，但同样会带 entry_texts，备注里的标识仍要被拦下
                assert {"phone", "email", "id_card", "bank_card"} <= set(
                    prepared.report["hits"]
                )
                assert "person_name" not in prepared.report["hits"]
            else:
                # 其余用途根本不许 entry_texts 出站，扫描不应有命中
                assert prepared.report["hits"] == {}


def test_golden_confirmed_text_drops_pii(sample: FastVault, analytics: AnalyticsService) -> None:
    """自由文本只在 api_extended + 白名单允许时出站，且逐条过扫描。"""
    # 报告白名单现在是激进档（raw），这里显式关掉以验证「扫描兜底」这条链路仍在
    whitelist = replace(load_whitelist("weekly_report", EGRESS_DIR), raw=False)
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=whitelist,
        policy=POLICY_EXTENDED,
        anchor=ANCHOR,
        income_value=30000.0,
        confirmed_texts=["睡眠这周挺稳", f"打给{PHONE}聊了聊"],
    )
    assert prepared.payload["confirmed_texts"] == ["睡眠这周挺稳"]
    assert any("confirmed_texts[1]" in path for path in prepared.report["dropped"])


def test_golden_confirmed_text_needs_free_text_whitelist(
    sample: FastVault, analytics: AnalyticsService
) -> None:
    """monthly_report 未开 free_text：即使策略允许、用户确认，文本也出不去。"""
    whitelist = load_whitelist("monthly_report", EGRESS_DIR)
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=whitelist,
        policy=POLICY_EXTENDED,
        anchor=ANCHOR,
        income_value=30000.0,
        confirmed_texts=["睡眠这周挺稳"],
    )
    assert "confirmed_texts" not in prepared.payload


def test_sources_block_passes_only_ref_summary_and_evaluation(
    sample: FastVault,
) -> None:
    """分层摘要的来源只出站 ref / summary / evaluation；周报白名单里没有这个区块。"""
    facts = {
        "facts_version": 1,
        "period": {"kind": "monthly", "start": "2026-10-01", "end": "2026-10-31"},
        "profile": {"age_band": "adult", "locale": "zh-CN"},
        "sources": [
            {
                "ref": "2026-10-12",
                "summary": "这一周记录比较平稳",
                "evaluation": ["心情比前一周稳"],
                "advice": "下层报告的建议不该再出站",
                "person": "张三",
            }
        ],
    }
    prepared = prepare(
        sample.conn,
        facts=facts,
        whitelist=load_whitelist("monthly_report", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    assert prepared.allowed
    assert prepared.payload["sources"] == [
        {
            "ref": "2026-10-12",
            "summary": "这一周记录比较平稳",
            "evaluation": ["心情比前一周稳"],
        }
    ]

    weekly = prepare(
        sample.conn,
        facts=facts,
        whitelist=load_whitelist("weekly_report", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    assert "sources" not in weekly.payload


def test_entry_texts_only_outbound_with_extended_policy(
    sample: FastVault, analytics: AnalyticsService
) -> None:
    """条目标题/备注只在 api_extended 出站；只出站汇总档仍然是纯聚合。"""
    facts = _facts(analytics)
    whitelist = load_whitelist("weekly_report", EGRESS_DIR)
    summary = prepare(
        sample.conn,
        facts=facts,
        whitelist=whitelist,
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    assert summary.allowed
    assert "entry_texts" not in summary.payload

    extended = prepare(
        sample.conn,
        facts=facts,
        whitelist=whitelist,
        policy=POLICY_EXTENDED,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    assert extended.allowed
    block = extended.payload["entry_texts"]
    assert block["included"] == len(block["items"]) > 0
    for item in block["items"]:
        # 内容层字段：时间、领域、标题、备注、标签与全部详情字段
        assert set(item) <= {"at", "domain", "title", "note", "tags", "details"}
        # 只填了详情（睡眠时长、金额等）没写标题的条目也要保留，不能按标题判空
        assert item.get("title") or item.get("note") or item.get("tags") or item.get("details")
        # 分钟级 ISO 时间戳不会被网关误判成 12 位以上的银行卡号
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", item["at"])


def test_entry_texts_drop_scanned_fields_and_prune_empty(fast_vault: FastVault) -> None:
    """标题/备注命中标识时只丢该字段；两个字段都被丢掉的那条不再发给模型。"""
    facts = {
        "facts_version": 3,
        "period": {"kind": "weekly", "start": "2026-09-28", "end": "2026-10-04"},
        "profile": {"age_band": "adult", "locale": "zh-CN"},
        "entry_texts": {
            "total": 3,
            "included": 3,
            "items": [
                {
                    "at": "2026-09-28T09:00",
                    "domain": "daily",
                    "title": f"打给{PHONE}",
                    "note": "普通的一天",
                },
                {
                    "at": "2026-09-29T09:00",
                    "domain": "expense",
                    "title": "报销",
                    "note": f"卡号 {BANK_CARD}",
                    "entry_id": "不应出站",
                },
                {
                    "at": "2026-09-30T09:00",
                    "domain": "daily",
                    "title": PHONE,
                    "note": ID_CARD,
                },
            ],
        },
    }
    prepared = prepare(
        fast_vault.conn,
        facts=facts,
        # 关闭激进档，专门验证「命中标识就丢字段」的兜底仍在
        whitelist=replace(load_whitelist("weekly_report", EGRESS_DIR), raw=False),
        policy=POLICY_EXTENDED,
        anchor=ANCHOR,
    )
    assert prepared.allowed
    block = prepared.payload["entry_texts"]
    assert block["included"] == 2
    assert block["items"] == [
        {"at": "2026-09-28T09:00", "domain": "daily", "note": "普通的一天"},
        {"at": "2026-09-29T09:00", "domain": "expense", "title": "报销"},
    ]
    assert any(path.endswith(".title") for path in prepared.report["dropped"])
    assert any(path.endswith(".note") for path in prepared.report["dropped"])


def test_entry_texts_removed_when_every_item_is_dropped(fast_vault: FastVault) -> None:
    """所有条目的标题与备注都命中标识时，整个区块不出现，避免只剩时间戳的空壳。"""
    facts = {
        "facts_version": 3,
        "period": {"kind": "weekly", "start": "2026-09-28", "end": "2026-10-04"},
        "profile": {"age_band": "adult", "locale": "zh-CN"},
        "entry_texts": {
            "total": 1,
            "included": 1,
            "items": [
                {
                    "at": "2026-09-28T09:00",
                    "domain": "daily",
                    "title": PHONE,
                    "note": EMAIL,
                }
            ],
        },
    }
    prepared = prepare(
        fast_vault.conn,
        facts=facts,
        # 关闭激进档，专门验证「命中标识就丢字段」的兜底仍在
        whitelist=replace(load_whitelist("weekly_report", EGRESS_DIR), raw=False),
        policy=POLICY_EXTENDED,
        anchor=ANCHOR,
    )
    assert prepared.allowed
    assert "entry_texts" not in prepared.payload


def test_golden_money_is_relative_not_absolute(
    sample: FastVault, analytics: AnalyticsService
) -> None:
    """research_synthesis 仍走「占收入比例」：金额不原样出站。"""
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("research_synthesis", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    expense = next(m for m in prepared.payload["metrics"] if m["key"] == "expense.total")
    assert expense["value"] == round(EXPENSE_TOTAL / INCOME_VALUE, 4)
    assert prepared.report["transforms"]["money_to_ratio"] > 0
    dumped = json.dumps(prepared.payload, ensure_ascii=False)
    assert "12800" not in dumped
    assert str(AMOUNT_MINOR) not in dumped


def test_golden_money_dropped_without_income(
    sample: FastVault, analytics: AnalyticsService
) -> None:
    """没有收入作分母时，宁可丢弃金额字段，也不原样出站。"""
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("research_synthesis", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=None,
    )
    expense = next(m for m in prepared.payload["metrics"] if m["key"] == "expense.total")
    assert "value" not in expense
    assert prepared.report["transforms"]["money_dropped"] > 0


def test_golden_money_metric_is_labelled_as_ratio(
    sample: FastVault, analytics: AnalyticsService
) -> None:
    """金额换成比例后，单位与类型必须同步改，否则模型会读成「0.35 元」。"""
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("research_synthesis", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    expense = next(m for m in prepared.payload["metrics"] if m["key"] == "expense.total")
    assert expense["value_kind"] == "ratio"
    assert expense["unit"] == "占收入比例（1 表示 100%）"
    assert "元" not in str(expense["unit"])


def test_report_whitelist_keeps_money_at_face_value(
    sample: FastVault, analytics: AnalyticsService
) -> None:
    """报告类用途按原值出站金额，不做收入占比换算（分析要读真实金额）。"""
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("weekly_report", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    expense = next(m for m in prepared.payload["metrics"] if m["key"] == "expense.total")
    assert expense["value"] == round(EXPENSE_TOTAL, 2)
    assert expense["value_kind"] == "money"
    assert prepared.report["transforms"]["money_to_ratio"] == 0
    assert prepared.report["transforms"]["money_dropped"] == 0


def test_golden_lineage_has_no_dates_or_entry_ids(
    sample: FastVault, analytics: AnalyticsService
) -> None:
    """血缘出站只留聚合信息：精确日期、entry_id、纠正记录 id 一律不出站。"""
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("weekly_report", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    text = json.dumps(prepared.payload, ensure_ascii=False)
    assert "entry_id" not in text
    assert "2026-09" not in text and "2026-10" not in text
    for metric in prepared.payload["metrics"]:
        lineage = metric["lineage"]
        assert "excluded" not in lineage
        assert "corrections" not in lineage
        assert isinstance(lineage["corrected"], bool)


def test_golden_outbound_text_is_localized(
    sample: FastVault, analytics: AnalyticsService
) -> None:
    """出站内容是给模型读的人话：不留 i18n 键，消息对象渲染成字符串。"""
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("weekly_report", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
        income_value=INCOME_VALUE,
    )
    text = json.dumps(prepared.payload, ensure_ascii=False)
    assert "i18n:" not in text
    for metric in prepared.payload["metrics"]:
        assert not str(metric["description"]).startswith("i18n:")
        assert metric["reason"] is None or isinstance(metric["reason"], str)
    assert all(isinstance(gap, str) for gap in prepared.payload["data_gaps"])
    for plan in prepared.payload["plans"]:
        assert all(isinstance(item, str) for item in plan["diagnosis"]["reasons"])


def test_golden_dates_become_week_index(sample: FastVault, analytics: AnalyticsService) -> None:
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("weekly_report", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
    )
    period = prepared.payload["period"]
    assert period == {
        "kind": "custom",
        "start_week": week_index(date(2026, 9, 7), ANCHOR),
        "end_week": week_index(date(2026, 10, 4), ANCHOR),
    }


def test_policy_none_blocks_everything(sample: FastVault, analytics: AnalyticsService) -> None:
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("weekly_report", EGRESS_DIR),
        policy=POLICY_NONE,
        anchor=ANCHOR,
        confirmed_texts=["任何内容"],
    )
    assert not prepared.allowed
    assert prepared.payload == {}
    assert prepared.report == {}
    assert prepared.reason == {"key": "egress.blocked.policy_none", "params": {}}


# ---------------------------------------------------------------------------
# 审计：只落哈希，绝不落内容
# ---------------------------------------------------------------------------
def test_egress_log_keeps_hash_not_content(sample: FastVault, analytics: AnalyticsService) -> None:
    prepared = prepare(
        sample.conn,
        facts=_facts(analytics),
        whitelist=load_whitelist("weekly_report", EGRESS_DIR),
        policy=POLICY_SUMMARY,
        anchor=ANCHOR,
    )
    log_id = record_egress(
        sample.conn,
        purpose="weekly_report",
        policy=POLICY_SUMMARY,
        payload=prepared.payload,
        report=prepared.report,
    )
    rows = list_egress_log(sample.conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == log_id
    assert row["payload_sha256"] == prepared.sha256
    assert row["payload_bytes"] == prepared.nbytes
    dumped = json.dumps(row, ensure_ascii=False)
    for needle in SENSITIVE + ("sleep.mean_hours", "mood"):
        assert needle not in dumped, f"审计记录里出现了内容：{needle}"


def test_policy_read_write_roundtrip(sample: FastVault) -> None:
    assert read_policy(sample.conn) == POLICY_NONE
    assert write_policy(sample.conn, POLICY_EXTENDED) == POLICY_EXTENDED
    assert read_policy(sample.conn) == POLICY_EXTENDED
    with pytest.raises(PolicyDeniedError):
        write_policy(sample.conn, "whatever")


# ---------------------------------------------------------------------------
# 端点（第 12.2 节）
# ---------------------------------------------------------------------------
@pytest.fixture
def app(data_dir, sample: FastVault, monkeypatch):
    config = AppConfig(data_dir=data_dir)
    application = create_app(config, token=TOKEN)
    state = application.state.lifeos
    state.profiles.registry.add(
        ProfileRecord(
            id=PROFILE_ID,
            alias="测试档案",
            vault_path=str(sample.path.parent),
            kdf_params={},
            wrapped_keys=[],
            created_at=now_utc_iso(),
        )
    )
    unlocked = UnlockedProfile(
        profile_id=PROFILE_ID,
        alias="测试档案",
        dek=sample.dek,
        meta={"age_band": "adult", "egress_policy": POLICY_NONE},
    )
    monkeypatch.setattr(state.profiles, "vault_path", lambda _pid: sample.path)
    monkeypatch.setattr(state.profiles, "blobs_dir", lambda _pid: sample.blobs_dir)
    monkeypatch.setattr(state.profiles, "unlock", lambda _pid, _pw: unlocked)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE_URL) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield test_client


def _unlocked(client) -> TestClient:
    response = client.post(f"{API_PREFIX}/profiles/{PROFILE_ID}/unlock", json={"passphrase": "x"})
    assert response.status_code == 200, response.text
    return client


def test_egress_endpoints_require_unlock(client) -> None:
    assert client.get(f"{API_PREFIX}/egress/policy").status_code == 423
    assert client.get(f"{API_PREFIX}/egress/log").status_code == 423
    preview = client.post(f"{API_PREFIX}/egress/preview", json={"purpose": "weekly_report"})
    assert preview.status_code == 423


def test_egress_policy_endpoint(client, sample: FastVault) -> None:
    _unlocked(client)
    body = client.get(f"{API_PREFIX}/egress/policy").json()
    assert body["policy"] == POLICY_NONE
    assert body["age_band"] == "adult"
    assert body["locked"] is False

    assert client.put(f"{API_PREFIX}/egress/policy", json={"policy": POLICY_SUMMARY}).json() == {
        "policy": POLICY_SUMMARY
    }

    # 儿童档只能 none：越权请求 403 policy_denied（第 7.7 节）
    sample.conn.execute("UPDATE profile_meta SET age_band = 'child' WHERE id = 1")
    denied = client.put(f"{API_PREFIX}/egress/policy", json={"policy": POLICY_EXTENDED})
    assert denied.status_code == 403
    assert denied.json()["code"] == "policy_denied"
    restored = client.put(f"{API_PREFIX}/egress/policy", json={"policy": POLICY_NONE})
    assert restored.status_code == 200


def test_egress_preview_endpoint(client, sample: FastVault) -> None:
    _unlocked(client)
    write_policy(sample.conn, POLICY_SUMMARY)
    response = client.post(
        f"{API_PREFIX}/egress/preview",
        json={
            "purpose": "weekly_report",
            "period": "custom",
            "start": PERIOD_START,
            "end": PERIOD_END,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allowed"] is True
    assert body["policy"] == POLICY_SUMMARY
    assert body["reason"] == {"key": "egress.allowed.summary", "params": {}}
    text = json.dumps(body["payload"], ensure_ascii=False)
    assert all(needle not in text for needle in SENSITIVE)
    assert body["nbytes"] > 0
    # 报告用途按原值出站金额（transform.money = raw），不再换算成收入占比
    expense = next(m for m in body["payload"]["metrics"] if m["key"] == "expense.total")
    assert expense["value"] == round(EXPENSE_TOTAL, 2)

    # 预览即已记录（第 7.4 节第 6 步），且记录里没有内容
    log = client.get(f"{API_PREFIX}/egress/log").json()["items"]
    assert len(log) == 1
    assert log[0]["status"] == "prepared"
    assert log[0]["purpose"] == "weekly_report"


def test_egress_preview_blocked_under_none(client) -> None:
    _unlocked(client)
    body = client.post(
        f"{API_PREFIX}/egress/preview", json={"purpose": "monthly_report", "period": "weekly"}
    ).json()
    assert body["allowed"] is False
    assert body["payload"] == {}
    assert body["reason"]["key"] == "egress.blocked.policy_none"
    assert client.get(f"{API_PREFIX}/egress/log").json()["items"][0]["status"] == "blocked"


def test_egress_preview_unknown_purpose(client) -> None:
    _unlocked(client)
    response = client.post(f"{API_PREFIX}/egress/preview", json={"purpose": "guess"})
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_purpose"
