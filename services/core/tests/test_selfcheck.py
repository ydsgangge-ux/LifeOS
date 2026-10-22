"""自我认知校验模块测试（第 16 章、P1.5）。

覆盖：检测器门槛与缺口、假设生成与三条底线校验、安全阀（年龄档/频率/4 周
阻击/暂停）、判断回查，以及「儿童与青少年档不出现该模块」。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from lifeos.domain.errors import InvalidInputError, NotFoundError
from lifeos.selfcheck import SelfCheckService, safety
from lifeos.selfcheck import hypothesis as hyp_mod
from lifeos.util.ids import now_utc_iso

AS_OF = date(2026, 6, 1)


def _insert_entry(conn, eid: str, domain: str, day: str, title: str | None = None) -> None:
    now = now_utc_iso()
    conn.execute(
        "INSERT INTO entry (id, domain, occurred_at, occurred_date, title, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (eid, domain, f"{day}T08:00:00+08:00", day, title, now, now),
    )


def _insert_plan(
    conn,
    pid: str,
    *,
    status: str,
    created: str,
    closed: str | None = None,
    goal_id: str | None = None,
) -> None:
    now = now_utc_iso()
    conn.execute(
        "INSERT INTO plan (id, goal_id, title, status, started_at, closed_at,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pid,
            goal_id,
            pid,
            status,
            f"{created}T00:00:00+00:00",
            f"{closed}T00:00:00+00:00" if closed else None,
            f"{created}T00:00:00+00:00",
            now,
        ),
    )


def _service(vault) -> SelfCheckService:
    from lifeos.config import load_config

    return SelfCheckService(vault.conn, load_config(vault.path.parent))


def _enable(service: SelfCheckService) -> None:
    safety.write_settings(service.conn, enabled=True)


def _seed_plans(vault) -> None:
    conn = vault.conn
    _insert_plan(conn, "P1", status="done", created="2026-01-10", closed="2026-02-01")
    _insert_plan(conn, "P2", status="done", created="2026-02-15", closed="2026-03-10")
    _insert_plan(conn, "P3", status="dropped", created="2026-03-01", closed="2026-03-20")
    _insert_plan(conn, "P4", status="dropped", created="2026-04-01", closed="2026-04-20")
    _insert_plan(conn, "P5", status="active", created="2026-05-01")


# ---------------------------------------------------------------------------
# 检测器
# ---------------------------------------------------------------------------
def test_start_vs_finish_builds_hypothesis(fast_vault):
    _seed_plans(fast_vault)
    service = _service(fast_vault)
    _enable(service)
    claim = service.create_claim(statement="我执行力很强", kind="strength")
    out = service.generate(claim["id"], as_of=AS_OF)
    assert out["blocked"] is None
    hyp = out["hypotheses"][0]
    assert hyp["pattern_key"] == "start_vs_finish"
    assert hyp["alternatives"] and len(hyp["alternatives"]) >= 2
    assert any(a["compatible_with_claim"] for a in hyp["alternatives"])
    assert hyp["falsifier"]
    kinds = {e["kind"] for e in hyp["evidence"]}
    assert "supports" in kinds and "counterexample" in kinds
    # 规则证据的说明必须是 {key, params} 消息对象，且键能在词表里查全、占位符都填上
    # （否则前端 renderMessage 拿到裸键，会把 selfcheck.ev.xxx 直接透到界面）
    from lifeos.i18n import t

    rule_ev = [e for e in hyp["evidence"] if e["added_by"] == "rule"]
    assert rule_ev
    for ev in rule_ev:
        note = ev["note"]
        assert isinstance(note, dict), note
        text = t(note["key"], **note["params"])
        assert text != note["key"] and "{" not in text, text
    # 枚举参数要换成词表标签，不能在中文句子里露出 dropped 这类原始枚举值
    statuses = [
        e["note"]["params"]["status"] for e in rule_ev if "status" in e["note"]["params"]
    ]
    assert statuses and all(s == t("goals.plan_status.dropped") for s in statuses), statuses
    # 生成时同时留了一条待回查的判断
    assert service.judgments()["stats"]["counts"].get("pending") == 1


def test_insufficient_data_only_reports_gaps(fast_vault):
    service = _service(fast_vault)
    _enable(service)
    claim = service.create_claim(statement="我执行力很强", kind="strength")
    out = service.generate(claim["id"], as_of=AS_OF)
    assert out["hypotheses"] == []
    assert out["gaps"], "数据不足时必须给出缺口"
    # 缺口文案必须能在词表里查到
    from lifeos.i18n import t

    assert t(out["gaps"][0]["key"]) != out["gaps"][0]["key"]


def test_ability_vs_trial_marks_small_sample(fast_vault):
    service = _service(fast_vault)
    _enable(service)
    claim = service.create_claim(statement="我不擅长跑步", kind="difficulty")
    out = service.generate(claim["id"], as_of=AS_OF, skill_id=None)
    assert out["hypotheses"] == []
    assert any(g["key"] == "selfcheck.miss.no_skill" for g in out["gaps"])


# ---------------------------------------------------------------------------
# 校验器（第 16.6 节）
# ---------------------------------------------------------------------------
def test_validator_rejects_banned_words():
    with pytest.raises(InvalidInputError):
        hyp_mod.validate(
            statement="你在逃避收尾这件事",
            alternatives=[
                {"text": "a", "compatible_with_claim": True},
                {"text": "b", "compatible_with_claim": False},
            ],
            falsifier="f",
        )


def test_validator_requires_compatible_alternative():
    with pytest.raises(InvalidInputError):
        hyp_mod.validate(
            statement="陈述",
            alternatives=[
                {"text": "a", "compatible_with_claim": False},
                {"text": "b", "compatible_with_claim": False},
            ],
            falsifier="f",
        )


# ---------------------------------------------------------------------------
# 安全阀（第 16.8 节）
# ---------------------------------------------------------------------------
def test_teen_profile_cannot_enable(fast_vault):
    fast_vault.conn.execute("UPDATE profile_meta SET age_band = 'teen' WHERE id = 1")
    service = _service(fast_vault)
    with pytest.raises(InvalidInputError):
        service.update_settings(enabled=True)
    assert safety.read_settings(fast_vault.conn)["allowed"] is False


def test_generate_blocked_when_disabled(fast_vault):
    _seed_plans(fast_vault)
    service = _service(fast_vault)
    claim = service.create_claim(statement="我执行力很强", kind="strength")
    out = service.generate(claim["id"], as_of=AS_OF)
    assert out["hypotheses"] == []
    assert out["blocked"]["key"] == "selfcheck.block.disabled"


def test_weekly_limit_blocks_extra_hypotheses(fast_vault):
    _seed_plans(fast_vault)
    service = _service(fast_vault)
    _enable(service)
    service.update_settings(weekly_limit=1)
    claim = service.create_claim(statement="我执行力很强", kind="strength")
    first = service.generate(claim["id"], as_of=AS_OF)
    assert len(first["hypotheses"]) == 1
    second = service.generate(claim["id"], as_of=AS_OF)
    assert second["hypotheses"] == []
    assert second["blocked"]["key"] == "selfcheck.block.weekly_limit"


def test_rejection_cools_down_for_four_weeks(fast_vault):
    _seed_plans(fast_vault)
    service = _service(fast_vault)
    _enable(service)
    service.update_settings(weekly_limit=4)
    claim = service.create_claim(statement="我执行力很强", kind="strength")
    first = service.generate(claim["id"], as_of=AS_OF)
    hyp_id = first["hypotheses"][0]["id"]
    rejected = service.respond(hyp_id, action="reject", note="这周在休假", as_of=AS_OF)
    assert rejected["status"] == "user_rejected"
    assert service.judgments()["stats"]["counts"].get("user_overrode") == 1
    again = service.generate(claim["id"], as_of=AS_OF)
    assert again["blocked"]["key"] == "selfcheck.block.recent_rejection"
    # 4 周之后可以重新提出
    later = service.generate(claim["id"], as_of=AS_OF + timedelta(days=29))
    assert len(later["hypotheses"]) == 1


def test_pause_on_wellbeing(fast_vault):
    service = _service(fast_vault)
    _enable(service)
    for i in range(7):
        day = (AS_OF - timedelta(days=i + 1)).isoformat()
        _insert_entry(fast_vault.conn, f"D{i}", "daily", day)
        fast_vault.conn.execute(
            "INSERT INTO daily_log (entry_id, stress) VALUES (?, ?)", (f"D{i}", 5)
        )
    reason = safety.pause_reason(fast_vault.conn, AS_OF)
    assert reason is not None and reason["key"] == "selfcheck.pause.wellbeing"


def test_respond_context_keeps_status(fast_vault):
    _seed_plans(fast_vault)
    service = _service(fast_vault)
    _enable(service)
    claim = service.create_claim(statement="我执行力很强", kind="strength")
    hyp = service.generate(claim["id"], as_of=AS_OF)["hypotheses"][0]
    after = service.respond(hyp["id"], action="context", note="中间搬过一次家")
    assert after["status"] == "proposed"
    assert any(e["kind"] == "user_rebuttal" for e in after["evidence"])


def test_missing_hypothesis_raises_not_found(fast_vault):
    service = _service(fast_vault)
    with pytest.raises(NotFoundError):
        service.respond("NOPE", action="accept")


# ---------------------------------------------------------------------------
# 判断回查（第 16.5 节第 6 步）
# ---------------------------------------------------------------------------
def test_due_checks_resolve_pending(fast_vault):
    _seed_plans(fast_vault)
    service = _service(fast_vault)
    _enable(service)
    claim = service.create_claim(statement="我执行力很强", kind="strength")
    hyp = service.generate(claim["id"], as_of=AS_OF)["hypotheses"][0]
    service.respond(hyp["id"], action="accept")
    checked = service.run_due_checks(as_of=AS_OF + timedelta(weeks=11))
    assert checked and checked[0]["outcome"] == "held"


# ---------------------------------------------------------------------------
# i18n 守卫：生成时用到的键必须都在共享词表里
# ---------------------------------------------------------------------------
def test_selfcheck_template_keys_exist():
    from lifeos.i18n import t

    missing = [key for key in hyp_mod.USED_KEYS if t(key) == key]
    assert missing == []
