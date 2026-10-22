"""指标计算引擎（第 8.2 / 8.3 / 8.9 节）。

- 只读数据库：不改任何业务表。
- 排除意外：按 anomaly 的 exclusion_mode 与 excluded_metrics 通配符，只在
  该意外的观察期内、只对匹配的指标生效；同时给出排除前后的值与差异（第 5.2 节）。
- 血缘：每个指标附来源（表.列 或公式 ID）、被排除的条目及原因、适用的用户纠正。
- 用户纠正：exclusion_override 可恢复被默认排除的记录，或手动排除一条（第 8.10 节）。

公式类指标与列类指标共用同一套排除逻辑：把「被排除的日期区间」当作一行行的
过滤条件。计划完成率永不排除意外（exclude_anomalies: false）。
"""

from __future__ import annotations

import fnmatch
import json
from datetime import date
from typing import Any

from ..db.connection import VaultConnection
from ..domain.errors import InvalidInputError
from ..i18n import message, render
from . import periods as P
from . import stats
from .registry import MetricDef, MetricRegistry

_MONEY_TABLES = frozenset(("txn",))


def _matches_metric(metric_key: str, patterns: list[str]) -> bool:
    """通配符匹配：支持 fnmatch 的 *、?、[]；无通配符时按前缀（含点号）匹配。

    这样「daily.mood」能匹配「daily.mood.mean」，「health.*」能匹配「health.sleep.std」。
    """
    for pattern in patterns:
        if pattern == metric_key:
            return True
        if any(ch in pattern for ch in "*?[]"):
            if fnmatch.fnmatchcase(metric_key, pattern):
                return True
        elif metric_key.startswith(pattern + "."):
            return True
    return False


class MetricEngine:
    """在给定 vault 连接上计算指标。"""

    def __init__(
        self,
        conn: VaultConnection,
        registry: MetricRegistry,
        *,
        base_currency: str = "CNY",
        base_minor_exp: int = 2,
    ) -> None:
        self._conn = conn
        self.registry = registry
        self.base_currency = base_currency
        self.base_minor_exp = base_minor_exp

    # ------------------------------------------------------------------
    # 意外排除与用户纠正
    # ------------------------------------------------------------------
    def _anomaly_ranges(self, metric_key: str) -> list[dict[str, Any]]:
        rows = self._conn.query(
            """
            SELECT a.id AS anomaly_id, a.type AS anomaly_type, a.exclusion_mode,
                   a.excluded_metrics_json, a.effect_start, a.effect_end,
                   ea.occurred_date AS anomaly_date, a.cause_note
            FROM anomaly a JOIN entry ea ON ea.id = a.entry_id
            WHERE ea.deleted_at IS NULL
            """
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            mode = r["exclusion_mode"]
            if mode == "none":
                continue
            if mode == "metrics":
                patterns = json.loads(r["excluded_metrics_json"] or "[]")
                if not _matches_metric(metric_key, patterns):
                    continue
            d0 = r["effect_start"] or r["anomaly_date"]
            d1 = r["effect_end"] or r["anomaly_date"]
            d0, d1 = str(d0)[:10], str(d1)[:10]
            if d0 > d1:
                d0, d1 = d1, d0
            out.append(
                {
                    "anomaly_id": r["anomaly_id"],
                    "anomaly_type": r["anomaly_type"],
                    "d0": d0,
                    "d1": d1,
                    "reason": (
                        r["cause_note"]
                        or message("facts.anomaly.reason_default", type=r["anomaly_type"])
                    ),
                }
            )
        out.sort(key=lambda x: (x["d0"], x["anomaly_id"]))
        return out

    def _corrections(self, metric_key: str) -> tuple[set[str], set[str], list[dict[str, Any]]]:
        """返回 (restore_dates, exclude_dates, applied)。"""
        rows = self._conn.query(
            "SELECT * FROM user_correction WHERE kind = 'exclusion_override'"
            " AND revoked_at IS NULL ORDER BY created_at"
        )
        restore: set[str] = set()
        exclude: set[str] = set()
        applied: list[dict[str, Any]] = []
        for r in rows:
            scope = json.loads(r["scope_json"] or "{}")
            target_metric = scope.get("metric") or "*"
            if target_metric != "*" and target_metric != metric_key:
                continue
            if r["target_type"] != "entry":
                continue
            entry = self._conn.query_one(
                "SELECT occurred_date FROM entry WHERE id = ?", (r["target_id"],)
            )
            if entry is None:
                continue
            when = str(entry["occurred_date"])[:10]
            action = scope.get("action", "restore")
            if action == "exclude":
                exclude.add(when)
            else:
                restore.add(when)
            applied.append(
                {
                    "correction_id": r["id"],
                    "kind": r["kind"],
                    "action": action,
                    "target_id": r["target_id"],
                    "date": when,
                    "reason": r["reason"],
                }
            )
        return restore, exclude, applied

    def _exclusion(self, metric_def: MetricDef) -> dict[str, Any]:
        if not metric_def.exclude_anomalies:
            return {"ranges": [], "restore": set(), "exclude": set(), "applied": []}
        restore, exclude, applied = self._corrections(metric_def.key)
        return {
            "ranges": self._anomaly_ranges(metric_def.key),
            "restore": restore,
            "exclude": exclude,
            "applied": applied,
        }

    @staticmethod
    def _is_excluded(when: str, exclusion: dict[str, Any]) -> bool:
        if when in exclusion["restore"]:
            return False
        if when in exclusion["exclude"]:
            return True
        return any(r["d0"] <= when <= r["d1"] for r in exclusion["ranges"])

    # ------------------------------------------------------------------
    # 取数
    # ------------------------------------------------------------------
    def _to_base_minor(
        self, amount_minor: int | None, currency: str, minor_exp: int, fx_rate: float | None
    ) -> int | None:
        if amount_minor is None:
            return None
        if currency == self.base_currency and minor_exp == self.base_minor_exp:
            return int(amount_minor)
        if currency != self.base_currency:
            if fx_rate is None:
                return None
            amount = float(amount_minor) * float(fx_rate)
        else:
            amount = float(amount_minor)
        scale = 10 ** (self.base_minor_exp - minor_exp)
        return int(round(amount * scale))

    def _column_rows(self, metric_def: MetricDef, start: date, end: date) -> list[dict[str, Any]]:
        table = metric_def.source_table
        column = metric_def.source_column
        assert table is not None and column is not None
        extra = ""
        if metric_def.value_kind == "money" and table in _MONEY_TABLES:
            extra = ", t.currency AS currency, t.minor_exp AS minor_exp,"
            extra += " t.fx_rate_to_base AS fx_rate"
        where = [
            "e.deleted_at IS NULL",
            "e.status = 'final'",
            "e.occurred_date >= ?",
            "e.occurred_date <= ?",
            f"t.{column} IS NOT NULL",
        ]
        params: list[Any] = [start.isoformat(), end.isoformat()]
        for col, val in metric_def.row_filter.items():
            where.append(f"t.{col} = ?")
            params.append(int(val) if isinstance(val, bool) else val)
        sql = (
            f"SELECT e.id AS entry_id, e.occurred_date AS occurred_date,"
            f" t.{column} AS raw{extra}"
            f" FROM {table} t JOIN entry e ON e.id = t.entry_id"
            f" WHERE {' AND '.join(where)}"
        )
        rows: list[dict[str, Any]] = []
        for r in self._conn.query(sql, params):
            when = str(r["occurred_date"])[:10]
            if metric_def.value_kind == "money":
                base = self._to_base_minor(
                    r["raw"], r["currency"], int(r["minor_exp"]), r["fx_rate"]
                )
                rows.append(
                    {"entry_id": r["entry_id"], "date": when, "money_minor": base, "raw": r["raw"]}
                )
            else:
                rows.append(
                    {
                        "entry_id": r["entry_id"],
                        "date": when,
                        "value": float(r["raw"]),
                        "raw": r["raw"],
                    }
                )
        rows.sort(key=lambda x: (x["date"], x["entry_id"]))
        return rows

    # ------------------------------------------------------------------
    # 聚合
    # ------------------------------------------------------------------
    def _aggregate(self, metric_def: MetricDef, rows: list[dict[str, Any]]) -> float | None:
        if metric_def.value_kind == "money":
            total = sum(int(r["money_minor"]) for r in rows if r["money_minor"] is not None)
            return total / (10**self.base_minor_exp)
        values = [float(r["value"]) for r in rows if r["value"] is not None]
        if metric_def.agg == "sum":
            return sum(values) if values else None
        if metric_def.agg == "std":
            return stats.sample_std(values)
        return stats.mean(values)

    def _n_of(self, metric_def: MetricDef, rows: list[dict[str, Any]]) -> int:
        if metric_def.value_kind == "money":
            return sum(1 for r in rows if r["money_minor"] is not None)
        return sum(1 for r in rows if r["value"] is not None)

    # ------------------------------------------------------------------
    # 公式
    # ------------------------------------------------------------------
    def _formula_rows(self, metric_def: MetricDef, start: date, end: date) -> list[dict[str, Any]]:
        """公式类指标的基础行（带日期），供周期与日粒度共用。"""
        formula = metric_def.formula
        params = metric_def.params
        if formula == "completeness":
            return self._distinct_days(str(params["table"]), None, start, end)
        if formula == "active_days":
            return self._distinct_days(
                str(params["table"]), str(params["column"]), start, end, positive=True
            )
        if formula == "nonessential_ratio":
            return self._txn_rows(start, end)
        if formula == "savings_rate":
            return self._txn_rows(start, end)
        if formula == "completion_rate":
            return self._plan_rows(start, end)
        if formula == "anomaly_count":
            return self._anomaly_rows(start, end)
        raise InvalidInputError(f"未知公式：{formula}")

    def _distinct_days(
        self,
        table: str,
        column: str | None,
        start: date,
        end: date,
        *,
        positive: bool = False,
    ) -> list[dict[str, Any]]:
        where = ["e.deleted_at IS NULL", "e.status = 'final'",
                 "e.occurred_date >= ?", "e.occurred_date <= ?"]
        params: list[Any] = [start.isoformat(), end.isoformat()]
        if column:
            where.append(f"t.{column} IS NOT NULL")
            if positive:
                where.append(f"t.{column} > 0")
        sql = (
            f"SELECT DISTINCT e.occurred_date AS d FROM {table} t"
            f" JOIN entry e ON e.id = t.entry_id WHERE {' AND '.join(where)}"
        )
        return [
            {"date": str(r["d"])[:10], "entry_id": str(r["d"])[:10]}
            for r in self._conn.query(sql, params)
        ]

    def _txn_rows(self, start: date, end: date) -> list[dict[str, Any]]:
        sql = (
            "SELECT e.id AS entry_id, e.occurred_date AS d, t.direction AS direction,"
            " t.amount_minor AS amt, t.currency AS currency, t.minor_exp AS minor_exp,"
            " t.fx_rate_to_base AS fx, t.essential AS essential"
            " FROM txn t JOIN entry e ON e.id = t.entry_id"
            " WHERE e.deleted_at IS NULL AND e.status = 'final'"
            " AND e.occurred_date >= ? AND e.occurred_date <= ?"
            " AND t.amount_minor IS NOT NULL"
        )
        out = []
        for r in self._conn.query(sql, [start.isoformat(), end.isoformat()]):
            out.append(
                {
                    "entry_id": r["entry_id"],
                    "date": str(r["d"])[:10],
                    "direction": r["direction"],
                    "money_minor": self._to_base_minor(
                        r["amt"], r["currency"], int(r["minor_exp"]), r["fx"]
                    ),
                    "essential": r["essential"],
                }
            )
        return out

    def _plan_rows(self, start: date, end: date) -> list[dict[str, Any]]:
        sql = (
            "SELECT e.id AS entry_id, e.occurred_date AS d, pr.planned_units AS planned,"
            " pr.actual_units AS actual"
            " FROM plan_record pr JOIN entry e ON e.id = pr.entry_id"
            " WHERE e.deleted_at IS NULL AND e.status = 'final'"
            " AND e.occurred_date >= ? AND e.occurred_date <= ?"
        )
        return [
            {
                "entry_id": r["entry_id"],
                "date": str(r["d"])[:10],
                "planned": r["planned"],
                "actual": r["actual"],
            }
            for r in self._conn.query(sql, [start.isoformat(), end.isoformat()])
        ]

    def _anomaly_rows(self, start: date, end: date) -> list[dict[str, Any]]:
        sql = (
            "SELECT e.id AS entry_id, e.occurred_date AS d, a.impact AS impact"
            " FROM anomaly a JOIN entry e ON e.id = a.entry_id"
            " WHERE e.deleted_at IS NULL"
            " AND e.occurred_date >= ? AND e.occurred_date <= ?"
        )
        return [
            {"entry_id": r["entry_id"], "date": str(r["d"])[:10], "impact": int(r["impact"])}
            for r in self._conn.query(sql, [start.isoformat(), end.isoformat()])
        ]

    @staticmethod
    def _keep(rows: list[dict[str, Any]], exclusion: dict[str, Any]) -> list[dict[str, Any]]:
        return [r for r in rows if not MetricEngine._is_excluded(r["date"], exclusion)]

    def _formula_value(
        self, metric_def: MetricDef, rows: list[dict[str, Any]], start: date, end: date
    ) -> tuple[float | None, int, dict[str, Any] | None]:
        """返回 (value, n, reason)。"""
        formula = metric_def.formula
        days = P.period_days(start, end)
        if formula == "completeness":
            covered = len({r["date"] for r in rows})
            return covered / days, days, None
        if formula == "active_days":
            return float(len({r["date"] for r in rows})), days, None
        if formula == "anomaly_count":
            return float(len(rows)), len(rows), None
        if formula == "nonessential_ratio":
            num = sum(int(r["money_minor"]) for r in rows
                      if r["direction"] == "out" and not r["essential"] and r["money_minor"])
            den = sum(int(r["money_minor"]) for r in rows
                      if r["direction"] == "out" and r["money_minor"])
            n = sum(1 for r in rows if r["direction"] == "out")
            if den == 0:
                return None, n, message("facts.metric.no_expense")
            return num / den, n, None
        if formula == "savings_rate":
            income = sum(int(r["money_minor"]) for r in rows
                         if r["direction"] == "in" and r["money_minor"])
            expense = sum(int(r["money_minor"]) for r in rows
                          if r["direction"] == "out" and r["money_minor"])
            if income == 0:
                return None, len(rows), message("facts.metric.no_income")
            return (income - expense) / income, len(rows), None
        if formula == "completion_rate":
            planned = sum(float(r["planned"]) for r in rows if r["planned"] is not None)
            actual = sum(float(r["actual"]) for r in rows if r["actual"] is not None)
            if planned <= 0:
                return None, len(rows), message("facts.metric.no_planned")
            return actual / planned, len(rows), None
        raise InvalidInputError(f"未知公式：{formula}")

    def _formula_series(
        self, metric_def: MetricDef, rows: list[dict[str, Any]], start: date, end: date
    ) -> dict[str, float]:
        """日粒度序列，供相关性使用。"""
        formula = metric_def.formula
        out: dict[str, float] = {}
        by_date: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        for when, group in by_date.items():
            if formula == "completeness" or formula == "active_days":
                out[when] = 1.0
            elif formula == "anomaly_count":
                out[when] = float(len(group))
            elif formula == "nonessential_ratio":
                den = sum(int(r["money_minor"]) for r in group
                          if r["direction"] == "out" and r["money_minor"])
                num = sum(int(r["money_minor"]) for r in group
                          if r["direction"] == "out" and not r["essential"] and r["money_minor"])
                if den:
                    out[when] = num / den
            elif formula == "savings_rate":
                income = sum(int(r["money_minor"]) for r in group
                             if r["direction"] == "in" and r["money_minor"])
                expense = sum(int(r["money_minor"]) for r in group
                              if r["direction"] == "out" and r["money_minor"])
                if income:
                    out[when] = (income - expense) / income
            elif formula == "completion_rate":
                planned = sum(float(r["planned"]) for r in group if r["planned"] is not None)
                actual = sum(float(r["actual"]) for r in group if r["actual"] is not None)
                if planned > 0:
                    out[when] = actual / planned
        return out

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def compute(self, key: str, start: date, end: date) -> dict[str, Any]:
        metric_def = self.registry.get(key)
        exclusion = self._exclusion(metric_def)
        line_excluded: list[dict[str, Any]] = []
        reason: dict[str, Any] | None = None

        if metric_def.is_formula:
            all_rows = self._formula_rows(metric_def, start, end)
            kept = self._keep(all_rows, exclusion)
            value, n, reason = self._formula_value(metric_def, kept, start, end)
            value_no_excl, _, _ = self._formula_value(metric_def, all_rows, start, end)
        else:
            all_rows = self._column_rows(metric_def, start, end)
            kept = self._keep(all_rows, exclusion)
            value = self._aggregate(metric_def, kept)
            value_no_excl = self._aggregate(metric_def, all_rows)
            n = self._n_of(metric_def, kept)

        kept_ids = {r["entry_id"] for r in kept}
        excluded_count = len({r["entry_id"] for r in all_rows} - kept_ids)
        seen: set[str] = set()
        for row in all_rows:
            eid = row["entry_id"]
            if eid in kept_ids or eid in seen:
                continue
            seen.add(eid)
            line_excluded.append(
                {
                    "entry_id": eid,
                    "date": row["date"],
                    "reason": _exclusion_reason(row["date"], exclusion),
                }
            )
        line_excluded.sort(key=lambda x: (x["date"], x["entry_id"]))

        status = "ok"
        if reason is not None:
            status = "not_computable"
        elif value is None or n < metric_def.min_n:
            status = "insufficient_data"
            reason = message("facts.metric.insufficient", n=n, min_n=metric_def.min_n)

        result = {
            "key": key,
            "description": metric_def.description,
            "unit": metric_def.unit,
            "status": status,
            "reason": reason,
            "value": value if status == "ok" else None,
            "value_without_exclusion": value_no_excl,
            "excluded_anomalies": excluded_count,
            "n": n,
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "lineage": self._lineage(metric_def, line_excluded, exclusion),
        }
        return result

    def daily_values(self, key: str, start: date, end: date) -> dict[str, float]:
        """日粒度值序列（用于趋势与相关性）。"""
        metric_def = self.registry.get(key)
        exclusion = self._exclusion(metric_def)
        if metric_def.is_formula:
            rows = self._keep(self._formula_rows(metric_def, start, end), exclusion)
            return self._formula_series(metric_def, rows, start, end)
        rows = self._keep(self._column_rows(metric_def, start, end), exclusion)
        by_date: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        out: dict[str, float] = {}
        for when, group in by_date.items():
            agg = self._aggregate(metric_def, group)
            if agg is not None and not (metric_def.agg == "std" and len(group) < 2):
                out[when] = agg
        return out

    def _lineage(
        self,
        metric_def: MetricDef,
        excluded: list[dict[str, Any]],
        exclusion: dict[str, Any],
    ) -> dict[str, Any]:
        source = None
        if metric_def.source_table:
            source = f"{metric_def.source_table}.{metric_def.source_column}"
        return {
            "source": source,
            "formula_id": metric_def.formula,
            "formula_params": metric_def.params,
            "excluded": excluded,
            "thresholds_ref": None,
            "corrections": exclusion.get("applied", []),
        }


def _exclusion_reason(when: str, exclusion: dict[str, Any]) -> str:
    if when in exclusion["exclude"]:
        return "用户手动排除"
    for r in exclusion["ranges"]:
        if r["d0"] <= when <= r["d1"]:
            reason = r["reason"]
            if isinstance(reason, dict):
                reason = render(reason)
            return f"{r['anomaly_type']} 意外（观察期 {r['d0']}..{r['d1']}）：{reason}"
    return "被排除"
