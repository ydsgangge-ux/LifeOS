"""自我认知校验服务门面（第 16 章）：把仓储、检测器、假设生成与安全阀组装成入口。

api 层只经这里读写 vault，不直接碰数据库（第 13.2 节）。事务边界在仓储层。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..config import AppConfig
from ..db.connection import VaultConnection
from ..domain.errors import InvalidInputError, NotFoundError
from ..i18n import DEFAULT_LOCALE, t
from ..util.ids import parse_date
from . import detectors, repository, safety
from . import hypothesis as hyp_mod

# 自述类型 → 对应的检测器（第 16.4 节各 pattern 对应的自述）
PATTERN_BY_KIND = {
    "strength": "start_vs_finish",
    "plan_belief": "forecast_vs_history",
    "value": "time_vs_values",
    "change": "change_claim",
    "difficulty": "ability_vs_trial",
}

DEFAULT_CHECK_WEEKS = 10

# 证据说明里的枚举参数 → 词表前缀（第 16.6 节），供 _localize_params 用。
_PARAM_LABEL_PREFIX = {"status": "goals.plan_status", "domain": "domain"}


class SelfCheckService:
    def __init__(self, conn: VaultConnection, config: AppConfig) -> None:
        self.conn = conn
        self.config = config

    # -- 设置 -------------------------------------------------------------
    def settings(self, *, as_of: date | None = None) -> dict[str, Any]:
        today = as_of or date.today()
        settings = safety.read_settings(self.conn)
        pause = safety.pause_reason(self.conn, today)
        stats = repository.judgment_stats(self.conn)
        settings["pause"] = pause
        settings["judgment_stats"] = stats
        return settings

    def update_settings(
        self,
        *,
        enabled: bool | None = None,
        paused: bool | None = None,
        weekly_limit: int | None = None,
    ) -> dict[str, Any]:
        if enabled is not None and enabled and not safety.age_band_allowed(self.conn):
            raise InvalidInputError("儿童与青少年档不提供自我认知校验")
        safety.write_settings(
            self.conn, enabled=enabled, paused=paused, weekly_limit=weekly_limit
        )
        return self.settings()

    # -- 自述 -------------------------------------------------------------
    def create_claim(
        self,
        *,
        statement: str,
        kind: str,
        domain: str | None = None,
        why_note: str | None = None,
        testable_as: str | None = None,
    ) -> dict[str, Any]:
        if kind not in repository.CLAIM_KINDS:
            raise InvalidInputError(f"未知自述类型：{kind}")
        if not statement.strip():
            raise InvalidInputError("自述内容不能为空")
        return repository.create_claim(
            self.conn,
            statement=statement.strip(),
            kind=kind,
            domain=domain,
            why_note=why_note,
            testable_as=testable_as,
        )

    def list_claims(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return repository.list_claims(self.conn, status=status)

    def update_claim(
        self,
        claim_id: str,
        *,
        testable_as: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        self._require_claim(claim_id)
        return repository.update_claim(
            self.conn, claim_id, testable_as=testable_as, status=status
        )

    def delete_claim(self, claim_id: str) -> None:
        self._require_claim(claim_id)
        repository.delete_claim(self.conn, claim_id)

    # -- 假设 -------------------------------------------------------------
    def list_hypotheses(
        self, *, claim_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        return repository.list_hypotheses(self.conn, claim_id=claim_id, status=status)

    def generate(
        self,
        claim_id: str,
        *,
        as_of: date | str | None = None,
        skill_id: str | None = None,
        metric_key: str | None = None,
        area: str | None = None,
        locale: str = DEFAULT_LOCALE,
    ) -> dict[str, Any]:
        """对一条自述跑对应检测器；门槛不够只出缺口，够则生成一条假设。"""
        claim = self._require_claim(claim_id)
        today = parse_date(as_of) if as_of is not None else date.today()
        pattern = PATTERN_BY_KIND.get(str(claim["kind"]), "start_vs_finish")
        result = self._run(
            pattern, claim, today, skill_id=skill_id, metric_key=metric_key, area=area
        )
        if not result.sufficient:
            return {
                "hypotheses": [],
                "gaps": result.gap or [{"key": "selfcheck.gap.insufficient", "params": {}}],
                "blocked": None,
            }
        blocked = safety.can_propose(
            self.conn, claim_id=claim_id, pattern_key=pattern, as_of=today
        )
        if blocked is not None:
            return {"hypotheses": [], "gaps": [], "blocked": blocked}
        built = hyp_mod.build(result, locale=locale, claim_domain=claim.get("domain"))
        if built is None:  # 理论上不会发生（sufficient 已判定）
            return {"hypotheses": [], "gaps": result.gap, "blocked": None}
        hyp = repository.create_hypothesis(
            self.conn,
            claim_id=claim_id,
            pattern_key=built["pattern_key"],
            statement=built["statement"],
            alternatives=built["alternatives"],
            falsifier=built["falsifier"],
            strength=built["strength"],
            coverage=built["coverage"],
            facts_snapshot_hash=built["facts_snapshot_hash"],
            # 传入 as_of 时也把它落到记录时间上，让时间窗（周频率、4 周阻击）
            # 能被确定性地测试；不传则用真实时钟。
            proposed_at=(
                f"{today.isoformat()}T00:00:00+00:00" if as_of is not None else None
            ),
        )
        self._save_evidence(hyp["id"], result, locale=locale)
        repository.log_judgment(
            self.conn,
            source="hypothesis",
            source_id=hyp["id"],
            statement=built["statement"],
            check_after=(today + timedelta(weeks=DEFAULT_CHECK_WEEKS)).isoformat(),
        )
        return {
            "hypotheses": [repository.get_hypothesis(self.conn, hyp["id"])],
            "gaps": [],
            "blocked": None,
        }

    def respond(
        self,
        hypothesis_id: str,
        *,
        action: str,
        note: str | None = None,
        as_of: date | str | None = None,
    ) -> dict[str, Any]:
        """接受、驳回或补充背景（第 16.5 节第 5 步）。"""
        try:
            bench = repository.get_hypothesis(self.conn, hypothesis_id)
        except KeyError as exc:
            raise NotFoundError(f"假设不存在：{hypothesis_id}") from exc
        # 复审时间与 as_of 对齐，供确定性测试 4 周阻击窗口。
        reviewed = (
            None if as_of is None else f"{parse_date(as_of).isoformat()}T00:00:00+00:00"
        )
        if action == "accept":
            return repository.set_hypothesis_status(
                self.conn, hypothesis_id, "user_accepted", reviewed_at=reviewed
            )
        if action == "reject":
            updated = repository.set_hypothesis_status(
                self.conn, hypothesis_id, "user_rejected", reviewed_at=reviewed
            )
            self._resolve_judgment(hypothesis_id, outcome="user_overrode", note=note)
            return updated
        if action == "context":
            # 补充的背景作为待检验的信息入库，状态不自动改变（第 16.6 节）。
            # 反驳往往没有对应记录，故以 metric 型引用挂在假设自己的检测器上；
            # metric 型不做存在性校验（见 0004 迁移的触发器）。
            repository.add_evidence(
                self.conn,
                hypothesis_id=hypothesis_id,
                kind="user_rebuttal",
                ref_type="metric",
                ref_id=str(bench["pattern_key"]),
                note=note,
                added_by="user",
            )
            return repository.get_hypothesis(self.conn, hypothesis_id)
        raise InvalidInputError(f"未知回应操作：{action}")

    # -- 判断留痕 ---------------------------------------------------------
    def judgments(self, *, limit: int = 100) -> dict[str, Any]:
        return {
            "stats": repository.judgment_stats(self.conn),
            "items": repository.list_judgments(self.conn, limit=limit),
        }

    def run_due_checks(self, *, as_of: date | str | None = None) -> list[dict[str, Any]]:
        """到期回查（第 16.5 节第 6 步）：把 pending 的判断按事实回填结果。"""
        today = parse_date(as_of) if as_of is not None else date.today()
        checked: list[dict[str, Any]] = []
        for judgment in repository.list_pending_judgments(self.conn, due_iso=today.isoformat()):
            outcome = "inconclusive"
            source_id = judgment.get("source_id")
            if judgment.get("source") == "hypothesis" and source_id:
                row = self.conn.query_one(
                    "SELECT status FROM hypothesis WHERE id = ?", (source_id,)
                )
                status = str(row["status"]) if row is not None else ""
                if status in ("supported", "user_accepted"):
                    outcome = "held"
                elif status in ("refuted", "user_rejected"):
                    outcome = "failed"
            repository.update_judgment(
                self.conn, str(judgment["id"]), outcome=outcome, outcome_at=today.isoformat()
            )
            checked.append({"id": judgment["id"], "outcome": outcome})
        return checked

    # -- 内部 -------------------------------------------------------------
    def _require_claim(self, claim_id: str) -> dict[str, Any]:
        try:
            return repository.get_claim(self.conn, claim_id)
        except KeyError as exc:
            raise NotFoundError(f"自述不存在：{claim_id}") from exc

    def _run(
        self,
        pattern: str,
        claim: dict[str, Any],
        today: date,
        *,
        skill_id: str | None,
        metric_key: str | None,
        area: str | None,
    ) -> detectors.DetectorResult:
        if pattern == "time_vs_values":
            return detectors.time_vs_values(
                self.conn, as_of=today, claim_domain=claim.get("domain")
            )
        if pattern == "ability_vs_trial":
            return detectors.ability_vs_trial(self.conn, as_of=today, skill_id=skill_id)
        if pattern == "forecast_vs_history":
            return detectors.forecast_vs_history(
                self.conn, as_of=today, area=area or claim.get("domain")
            )
        if pattern == "change_claim":
            return detectors.change_claim(self.conn, as_of=today, metric_key=metric_key)
        return detectors.start_vs_finish(self.conn, as_of=today)

    def _save_evidence(
        self, hypothesis_id: str, result: detectors.DetectorResult, *, locale: str
    ) -> None:
        for kind, items in (
            ("supports", result.supports),
            ("counterexample", result.counterexamples),
            ("context", result.contexts),
        ):
            for item in items:
                if item["ref_type"] == "metric" or _exists(
                    self.conn, item["ref_type"], item["ref_id"]
                ):
                    repository.add_evidence(
                        self.conn,
                        hypothesis_id=hypothesis_id,
                        kind=kind,
                        ref_type=item["ref_type"],
                        ref_id=item["ref_id"],
                        note=item["note_key"],
                        # 参数跟着一起存，读库时才能还原成 {key, params} 消息对象
                        # 交给渲染层本地化（NFR-08）。
                        note_params=_localize_params(item.get("note_params"), locale),
                    )

    def _resolve_judgment(self, hypothesis_id: str, *, outcome: str, note: str | None) -> None:
        row = self.conn.query_one(
            "SELECT id FROM judgment_log WHERE source = 'hypothesis' AND source_id = ?"
            " AND outcome = 'pending' ORDER BY made_at DESC LIMIT 1",
            (hypothesis_id,),
        )
        if row is not None:
            repository.update_judgment(self.conn, str(row["id"]), outcome=outcome, note=note)


def _localize_params(params: dict[str, Any] | None, locale: str) -> dict[str, Any] | None:
    """把证据说明里的枚举参数换成当前语言的标签。

    否则中文句子里会露出 ``dropped`` / ``education`` 这类英文枚举值（NFR-08）。
    """
    if not params:
        return None
    localized: dict[str, Any] = {}
    for key, value in params.items():
        prefix = _PARAM_LABEL_PREFIX.get(key)
        localized[key] = t(f"{prefix}.{value}", locale) if prefix and value else value
    return localized


def _exists(conn: VaultConnection, ref_type: str, ref_id: str) -> bool:
    table = {"entry": "entry", "plan": "plan", "goal": "goal"}.get(ref_type)
    if table is None:
        return True
    return conn.query_one(f"SELECT 1 FROM {table} WHERE id = ?", (ref_id,)) is not None
