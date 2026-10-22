"""页面级 AI 解读（第 16 章自我认知 / 第 11.1 节关系）。

覆盖：事实包只带聚合数字与代号（不发姓名、备注、原文）、策略 none 时降级并留痕、
注入假 transport 时的校验与占位符渲染，以及结果写入缓存表。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest
from conftest import build_vault

from lifeos.config import AppConfig
from lifeos.llm import gateway as llm_gateway
from lifeos.privacy import load_whitelist, write_policy
from lifeos.research import relation_review, review_runner, self_review
from lifeos.selfcheck import SelfCheckService
from lifeos.util.ids import now_utc_iso

PROVIDER = "openai"
MODEL = "gpt-4o-mini"
BASE_URL_LLM = "https://api.example/v1"
EGRESS_DIR = AppConfig().egress_dir


@pytest.fixture
def vault(tmp_path):
    built = build_vault(tmp_path)
    yield built
    built.conn.close()


# -- 事实包 -----------------------------------------------------------------
def _insert_entry(conn, eid: str, domain: str, day: str, title: str | None = None) -> None:
    now = now_utc_iso()
    conn.execute(
        "INSERT INTO entry (id, domain, occurred_at, occurred_date, title, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (eid, domain, f"{day}T08:00:00+08:00", day, title, now, now),
    )


def _insert_person(conn, pid: str, name: str, relation_type: str) -> None:
    now = now_utc_iso()
    conn.execute(
        "INSERT INTO person (id, code, display_name, relation_type, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (pid, pid, name, relation_type, now, now),
    )


def _insert_relation(
    conn,
    eid: str,
    pid: str,
    *,
    kind: str,
    quality: int,
    reciprocity: int,
    initiated_by: str,
) -> None:
    conn.execute(
        "INSERT INTO relation_record (entry_id, person_id, kind, quality, reciprocity,"
        " initiated_by) VALUES (?, ?, ?, ?, ?, ?)",
        (eid, pid, kind, quality, reciprocity, initiated_by),
    )


def _seed_relations(vault) -> None:
    conn = vault.conn
    today = date.today()
    for index in range(3):
        _insert_entry(
            conn, f"E{index}", "relation", (today - timedelta(days=index)).isoformat()
        )
    _insert_person(conn, "P1", "张三", "friend")
    _insert_person(conn, "P2", "李四", "colleague")
    _insert_relation(conn, "E0", "P1", kind="meet", quality=4, reciprocity=1, initiated_by="me")
    _insert_relation(conn, "E1", "P1", kind="call", quality=3, reciprocity=0, initiated_by="them")
    _insert_relation(
        conn, "E2", "P2", kind="conflict", quality=2, reciprocity=-1, initiated_by="them"
    )


def test_relation_facts_only_carry_codes(vault) -> None:
    """关系事实包只发人物代号与聚合数字，姓名与单条互动一律不出现在载荷里。"""
    _seed_relations(vault)
    facts = relation_review.build_facts(vault.conn)

    text = json.dumps(facts, ensure_ascii=False)
    assert "张三" not in text
    assert "李四" not in text
    assert [row["ref"] for row in facts["relation_people"]] == ["A", "B"]
    assert facts["relation_people"][0]["entry_count"] == 2
    assert facts["relation_overview"]["person_count"] == 2
    assert facts["relation_overview"]["conflict_count"] == 1
    # 逐月汇总固定 6 行（没有记录的月份补 0），越界索引不会让占位符解析失败
    assert len(facts["relation_months"]) == 6
    assert facts["relation_months"][-1]["month"] == date.today().strftime("%Y-%m")


def test_self_facts_drop_why_note(vault) -> None:
    """自我认知事实包只来自述文字、假设与计数，不含用户写的备注。"""
    checker = SelfCheckService(vault.conn, AppConfig())
    checker.create_claim(
        statement="我执行力很强",
        kind="strength",
        domain="career",
        why_note="备注里写了手机号 13800138000",
    )
    facts = self_review.build_facts(checker)

    text = json.dumps(facts, ensure_ascii=False)
    assert "13800138000" not in text
    assert "备注里" not in text
    assert facts["self_summary"]["claims_total"] == 1
    assert facts["self_summary"]["claims_open"] == 1
    assert facts["self_claims"][0]["kind"] == "strength"
    assert "why_note" not in facts["self_claims"][0]


# -- 降级 -------------------------------------------------------------------
def test_self_review_degrades_and_caches(vault) -> None:
    checker = SelfCheckService(vault.conn, AppConfig())
    checker.create_claim(statement="我执行力很强", kind="strength")
    write_policy(vault.conn, "none")

    result = self_review.review(checker, config=AppConfig())

    assert result["status"] == "degraded"
    assert result["reason"]["key"] == "egress.blocked.policy_none"
    assert result["egress_log_id"]
    cached = review_runner.read_cache(vault.conn, self_review.PURPOSE)
    assert cached is not None and cached["status"] == "degraded"


def test_relation_review_degrades_and_caches(vault) -> None:
    _seed_relations(vault)
    write_policy(vault.conn, "none")

    result = relation_review.review(vault.conn, config=AppConfig())

    assert result["status"] == "degraded"
    assert result["reason"]["key"] == "egress.blocked.policy_none"
    cached = review_runner.read_cache(vault.conn, relation_review.PURPOSE)
    assert cached is not None and cached["status"] == "degraded"


# -- 有模型 -----------------------------------------------------------------
def _transport(content: dict) -> httpx.MockTransport:
    body = {
        "model": MODEL,
        "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 600, "completion_tokens": 300},
    }
    return httpx.MockTransport(lambda _request: httpx.Response(200, json=body))


def _config() -> AppConfig:
    return AppConfig(llm_provider=PROVIDER, llm_model=MODEL, llm_base_url=BASE_URL_LLM)


SELF_OUTPUT = {
    "headline": "这次整理了 [[fact:self_summary.claims_total]] 条自述与 "
    "[[fact:self_summary.hypotheses_total]] 条假设。",
    "observations": [
        {
            "claim": "目前共有 [[fact:self_summary.claims_total]] 条自述在等你回看。",
            "figures": [{"ref": "self_summary.claims_total", "format": "integer"}],
            "evidence_refs": ["self_summary.claims_total"],
            "strength": "medium",
            "causal_claim": False,
            "alternatives": ["也可能只是最近才开始记录，样本还不够"],
        }
    ],
    "tensions": [],
    "questions": ["哪些记录是你最愿意补的？"],
    "experiments": [
        {
            "action": "每天补一条当天的记录",
            "success_criteria": "一周后能回看到连续几天的记录",
            "window_days": 7,
        }
    ],
    "uncertainty": ["目前的记录天数还不够下结论"],
}

RELATION_OUTPUT = {
    "headline": "这段窗口里共有 [[fact:relation_overview.entry_count]] 次互动记录，"
    "涉及 [[fact:relation_overview.person_count]] 位人物。",
    "patterns": [
        {
            "claim": "和人物 A 的互动最多，共有 [[fact:relation_people.0.entry_count]] 条记录。",
            "figures": [{"ref": "relation_people.0.entry_count", "format": "integer"}],
            "evidence_refs": ["relation_people.0.entry_count"],
            "strength": "medium",
            "causal_claim": False,
            "alternatives": ["也可能只是这段时间和人物 A 见面机会更多"],
        }
    ],
    "attention": [],
    "suggestions": [
        {
            "action": "每次互动后补一句质量与谁先开口",
            "success_criteria": "两周后能看到多数互动都有这两项",
            "window_days": 14,
        }
    ],
    "uncertainty": ["只记下了部分互动，未记录的情况不明"],
}


def test_self_review_ok_renders_placeholders(vault, monkeypatch) -> None:
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(vault.conn, "api_summary")
    checker = SelfCheckService(vault.conn, AppConfig())
    checker.create_claim(statement="我执行力很强", kind="strength")

    result = self_review.review(
        checker, config=_config(), transport=_transport(SELF_OUTPUT)
    )

    assert result["status"] == "ok"
    assert result["model"] == MODEL
    analysis = result["analysis"]
    assert "[[fact:" not in analysis["headline"]
    rendered = analysis["observations"][0]["rendered_claim"]
    assert "[[fact:" not in rendered
    assert "1 条" in rendered
    assert analysis["questions"] == SELF_OUTPUT["questions"]


def test_relation_review_ok_renders_placeholders(vault, monkeypatch) -> None:
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: "sk-test")
    write_policy(vault.conn, "api_summary")
    _seed_relations(vault)

    result = relation_review.review(
        vault.conn, config=_config(), transport=_transport(RELATION_OUTPUT)
    )

    assert result["status"] == "ok"
    analysis = result["analysis"]
    assert "[[fact:" not in analysis["headline"]
    rendered = analysis["patterns"][0]["rendered_claim"]
    assert "[[fact:" not in rendered
    assert "2 条" in rendered
    cached = review_runner.read_cache(vault.conn, relation_review.PURPOSE)
    assert cached is not None and cached["status"] == "ok"


# -- 白名单登记 --------------------------------------------------------------
def test_page_review_whitelists_are_registered() -> None:
    for purpose in ("self_review", "relation_review"):
        whitelist = load_whitelist(purpose, EGRESS_DIR)
        assert whitelist.purpose == purpose
        assert whitelist.free_text is False
        assert "income.total" not in whitelist.metrics
