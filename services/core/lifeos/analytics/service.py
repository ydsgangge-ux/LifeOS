"""分析服务门面（第 8 章）：指标、趋势、相关性、诊断、事实包的统一入口。

api 层通过它读取事实，不直接碰数据库。所有计算只读 vault，确定性且可解释。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db.connection import VaultConnection
from ..domain.errors import NotFoundError
from ..domain.guardian import OWNER_VIEWER, Viewer
from ..i18n import DEFAULT_LOCALE
from . import facts as facts_mod
from . import periods as P
from .correlation import CorrelationConfig, analyze, load_correlation_config
from .diagnosis import DiagnosisConfig, diagnose_plan, load_diagnosis_config
from .metrics import MetricEngine
from .registry import MetricRegistry


class AnalyticsService:
    """把注册表、配置与引擎组装成事实计算的入口。"""

    def __init__(
        self, conn: VaultConnection, config: AppConfig, viewer: Viewer | None = None
    ) -> None:
        self.conn = conn
        self.config = config
        # guardian 会话下事实包按可见范围裁剪（第 7.7 节、验收 A2）
        self.viewer = viewer or OWNER_VIEWER
        self.registry = MetricRegistry(config.metrics_dir)
        self.diagnosis_config: DiagnosisConfig = load_diagnosis_config(
            config.diagnosis_config
        )
        self.correlation_config: CorrelationConfig = load_correlation_config(
            Path(config.config_dir) / "correlation.yaml"
        )
        profile = conn.query_one(
            "SELECT base_currency, base_minor_exp, age_band, locale FROM profile_meta WHERE id = 1"
        )
        self.base_currency = str(profile["base_currency"]) if profile else "CNY"
        self.base_minor_exp = int(profile["base_minor_exp"]) if profile else 2
        self.age_band = str(profile["age_band"]) if profile else "adult"
        self.locale = str(profile["locale"]) if profile and profile["locale"] else DEFAULT_LOCALE
        self.engine = MetricEngine(
            conn,
            self.registry,
            base_currency=self.base_currency,
            base_minor_exp=self.base_minor_exp,
        )

    def reload_configs(self) -> None:
        self.registry.reload()
        self.diagnosis_config = load_diagnosis_config(self.config.diagnosis_config)
        self.correlation_config = load_correlation_config(
            Path(self.config.config_dir) / "correlation.yaml"
        )

    def _domains(self) -> frozenset[str] | None:
        """guardian 会话下按可见领域裁剪事实包；owner 返回 None（不裁剪）。"""
        return self.viewer.visible_domains

    def scope_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        """再读一份历史报告的事实包时，按当前可见范围复裁（验收 A2）。"""
        if not self.viewer.is_guardian:
            return facts
        return facts_mod.scope_facts(facts, self.viewer.policy, self.registry)

    # -- 端点支撑 ---------------------------------------------------------
    def metrics(
        self,
        *,
        kind: str,
        end: str | date | None = None,
        start: str | date | None = None,
        keys: list[str] | None = None,
    ) -> dict[str, Any]:
        start_d, end_d = P.resolve_period(kind, end=end, start=start)
        items = facts_mod.build_metrics(
            self.engine, self.registry, start_d, end_d, keys=keys, domains=self._domains()
        )
        return {
            "period": {
                "kind": kind,
                "start": start_d.isoformat(),
                "end": end_d.isoformat(),
            },
            "items": items,
        }

    def facts(
        self,
        *,
        kind: str,
        end: str | date | None = None,
        start: str | date | None = None,
    ) -> dict[str, Any]:
        start_d, end_d = P.resolve_period(kind, end=end, start=start)
        return facts_mod.build_facts(
            self.conn,
            self.registry,
            self.engine,
            self.correlation_config,
            self.diagnosis_config,
            kind=kind,
            start=start_d,
            end=end_d,
            age_band=self.age_band,
            locale=self.locale,
            domains=self._domains(),
        )

    def plan_diagnosis(
        self, plan_id: str, *, as_of: str | date | None = None
    ) -> dict[str, Any]:
        plan = self.conn.query_one(
            "SELECT * FROM plan WHERE id = ? AND deleted_at IS NULL", (plan_id,)
        )
        if plan is None:
            raise NotFoundError(f"计划不存在：{plan_id}")
        return diagnose_plan(
            self.conn,
            dict(plan),
            self.diagnosis_config,
            as_of=P.parse_date(as_of),
        )

    def explain(
        self,
        *,
        kind: str,
        end: str | date | None = None,
        start: str | date | None = None,
        metric: str | None = None,
        plan_id: str | None = None,
        insight_id: str | None = None,
    ) -> dict[str, Any]:
        """「为什么是这个值」反查（第 8.10 节）。"""
        start_d, end_d = P.resolve_period(kind, end=end, start=start)
        if metric:
            result = self.engine.compute(metric, start_d, end_d)
            return {
                "target": {"type": "metric", "id": metric},
                "period": {"start": start_d.isoformat(), "end": end_d.isoformat()},
                "value": result["value"],
                "value_without_exclusion": result["value_without_exclusion"],
                "excluded_anomalies": result["excluded_anomalies"],
                "n": result["n"],
                "status": result["status"],
                "reason": result["reason"],
                "lineage": result["lineage"],
            }
        if plan_id:
            diagnosis = self.plan_diagnosis(plan_id, as_of=end_d)
            return {
                "target": {"type": "diagnosis", "id": plan_id},
                "period": {"start": start_d.isoformat(), "end": end_d.isoformat()},
                "verdict": diagnosis["verdict"],
                "reasons": diagnosis["reasons"],
                "thresholds_ref": diagnosis["thresholds_ref"],
                "thresholds": diagnosis["thresholds"],
                "user_rejected": diagnosis["user_rejected"],
                "lineage": {
                    "source": "plan_record + goal + goal_checkpoint",
                    "excluded": [],
                    "corrections": [],
                },
            }
        if insight_id:
            block = analyze(self.conn, self.engine, self.correlation_config, start_d, end_d)
            for insight in block["insights"]:
                if insight["id"] == insight_id:
                    return {
                        "target": {"type": "insight", "id": insight_id},
                        "period": {"start": start_d.isoformat(), "end": end_d.isoformat()},
                        "insight": insight,
                        "lineage": insight["lineage"],
                    }
            raise NotFoundError(f"相关性提示不存在：{insight_id}")
        raise NotFoundError("explain 需要给出 metric、plan_id 或 insight_id 之一")
