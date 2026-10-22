"""相关性提示（第 8.5 节）：产出「待验证假设」，不是结论，也不是因果证明。

流程：
1. 候选指标对固定在 config/correlation.yaml，避免到处找相关。
2. 日粒度 Spearman，滞后取 lags（默认 0/1/2 天）；计算前先减去各自的星期均值。
3. 有效样本数 n_eff 按两个序列的一阶自相关估计；置信区间用 7 天为一块的
   block bootstrap（固定随机种子，保证同样数据永远得到同样区间）。
4. 配对覆盖率低于门槛不计算；并检查缺失是否随机，可疑时标出并降一级。
5. 门槛按 n_eff；输出前用 Benjamini-Hochberg 控制 FDR；|rho| 低于下限不报告。
6. 每个结果固定写 causal_claim: false，并附验证建议；只进事实包，不建关系边。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from ..db.connection import VaultConnection
from ..domain.errors import InvalidInputError
from ..i18n import message
from . import periods as P
from . import stats
from .metrics import MetricEngine

_LEVEL_ORDER = ("insufficient", "weak_signal", "signal")


@dataclass(frozen=True)
class CorrelationConfig:
    version: str
    lags: tuple[int, ...]
    min_coverage: float
    n_eff_signal: int
    n_eff_weak: int
    effect_size_min: float
    fdr_q: float
    block_days: int
    bootstrap_samples: int
    seed: int
    missing_std_ratio: float
    pairs: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def load_correlation_config(path: str | Path) -> CorrelationConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    params = raw.get("params", {})
    try:
        return CorrelationConfig(
            version=str(raw.get("version", "corr_v1")),
            lags=tuple(int(x) for x in params.get("lags", [0, 1, 2])),
            min_coverage=float(params.get("min_coverage", 0.7)),
            n_eff_signal=int(params.get("n_eff_signal", 20)),
            n_eff_weak=int(params.get("n_eff_weak", 10)),
            effect_size_min=float(params.get("effect_size_min", 0.3)),
            fdr_q=float(params.get("fdr_q", 0.1)),
            block_days=int(params.get("block_days", 7)),
            bootstrap_samples=int(params.get("bootstrap_samples", 500)),
            seed=int(params.get("seed", 20260101)),
            missing_std_ratio=float(params.get("missing_std_ratio", 0.5)),
            pairs=tuple(raw.get("pairs", [])),
        )
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"相关性配置非法：{path}") from exc


def _anomaly_dates(conn: VaultConnection, start: date, end: date) -> set[str]:
    """被任何意外观察期覆盖的日期（无论排除方式），用于单列已知因素。"""
    rows = conn.query(
        "SELECT a.effect_start AS s, a.effect_end AS e, ea.occurred_date AS d"
        " FROM anomaly a JOIN entry ea ON ea.id = a.entry_id"
        " WHERE ea.deleted_at IS NULL"
    )
    out: set[str] = set()
    for r in rows:
        d0 = str(r["s"] or r["d"])[:10]
        d1 = str(r["e"] or r["d"])[:10]
        if d0 > d1:
            d0, d1 = d1, d0
        day = date.fromisoformat(d0)
        last = date.fromisoformat(d1)
        while day <= last:
            if start <= day <= end:
                out.add(day.isoformat())
            day += timedelta(days=1)
    return out


def _level_for(n_eff: float, cfg: CorrelationConfig) -> str:
    if n_eff >= cfg.n_eff_signal:
        return "signal"
    if n_eff >= cfg.n_eff_weak:
        return "weak_signal"
    return "insufficient"


def _downgrade(level: str) -> str:
    idx = _LEVEL_ORDER.index(level)
    return _LEVEL_ORDER[max(0, idx - 1)]


def analyze(
    conn: VaultConnection, engine: MetricEngine, cfg: CorrelationConfig, start: date, end: date
) -> dict[str, Any]:
    lags = cfg.lags
    max_lag = max(lags) if lags else 0
    key_set: set[str] = set()
    for pair in cfg.pairs:
        predictor = pair.get("predictor")
        outcome = pair.get("outcome")
        if predictor:
            key_set.add(str(predictor))
        if outcome:
            key_set.add(str(outcome))
    series: dict[str, dict[str, float]] = {}
    for metric_key in sorted(key_set):
        try:
            raw = engine.daily_values(metric_key, start, end)
        except InvalidInputError:
            raw = {}
        series[metric_key] = stats.remove_weekday_effect(raw, P.weekday_of)
    anomaly_dates = _anomaly_dates(conn, start, end)
    span_days = P.each_day(start, end)

    candidates: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for pair in cfg.pairs:
        predictor = pair.get("predictor")
        outcome = pair.get("outcome")
        if predictor not in series or outcome not in series:
            gaps.append(
                message("corr.gap.metric_unregistered", predictor=predictor, outcome=outcome)
            )
            continue
        for lag in lags:
            cand = _pair_lag(
                pair, lag, series[predictor], series[outcome], span_days, max_lag,
                anomaly_dates, cfg,
            )
            if cand is None:
                gaps.append(
                    message("corr.gap.coverage", predictor=predictor, outcome=outcome, lag=lag)
                )
                continue
            candidates.append(cand)

    passed = stats.benjamini_hochberg([c["p_value"] for c in candidates], cfg.fdr_q)
    for cand, ok in zip(candidates, passed, strict=True):
        cand["fdr_pass"] = ok

    # 每个候选对只保留通过 FDR 且效应量达标、|rho| 最大的那个滞后。
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for cand in candidates:
        if not cand["fdr_pass"] or abs(cand["rho"]) < cfg.effect_size_min:
            continue
        if cand["level"] == "insufficient":
            continue
        key = (cand["pair"][0], cand["pair"][1])
        current = best.get(key)
        if current is None or abs(cand["rho"]) > abs(current["rho"]):
            best[key] = cand

    insights: list[dict[str, Any]] = []
    for i, cand in enumerate(sorted(best.values(), key=lambda c: (c["pair"][0], c["pair"][1])), 1):
        insights.append(_to_insight(i, cand, cfg))
    return {"version": cfg.version, "insights": insights, "gaps": gaps}


def _pair_lag(
    pair: dict[str, Any],
    lag: int,
    predictor: dict[str, float],
    outcome: dict[str, float],
    span_days: list[date],
    max_lag: int,
    anomaly_dates: set[str],
    cfg: CorrelationConfig,
) -> dict[str, Any] | None:
    horizon_end = span_days[-1] - timedelta(days=lag)
    domains = [d for d in span_days if d <= horizon_end]
    possible = [d for d in domains if d.isoformat() in predictor]
    if not possible:
        return None
    px: list[float] = []
    oy: list[float] = []
    matched_days: list[str] = []
    for d in possible:
        target = (d + timedelta(days=lag)).isoformat()
        if target in outcome:
            px.append(predictor[d.isoformat()])
            oy.append(outcome[target])
            matched_days.append(target)
    coverage = len(px) / len(possible)
    if coverage < cfg.min_coverage or len(px) < 3:
        return None
    rho = stats.spearman(px, oy)
    if rho is None:
        return None
    n_eff = stats.effective_sample_size(px, oy)
    level = _level_for(n_eff, cfg)
    if level == "insufficient":
        return None

    # 缺失是否随机：比较结果指标在预测指标缺失日与非缺失日的均值差。
    missing_targets = [
        (d + timedelta(days=lag)).isoformat()
        for d in domains
        if d.isoformat() not in predictor and (d + timedelta(days=lag)).isoformat() in outcome
    ]
    missing_flag = False
    missing_diff: float | None = None
    matched_outcome = list(oy)
    missing_outcome = [outcome[t] for t in missing_targets]
    if missing_outcome and matched_outcome:
        std = stats.sample_std(matched_outcome)
        m_matched = stats.mean(matched_outcome)
        m_missing = stats.mean(missing_outcome)
        if std and m_matched is not None and m_missing is not None:
            missing_diff = m_missing - m_matched
            if abs(missing_diff) > cfg.missing_std_ratio * std:
                missing_flag = True
                level = _downgrade(level)
                if level == "insufficient":
                    return None

    ci = stats.block_bootstrap_ci(
        px, oy, block_days=cfg.block_days, samples=cfg.bootstrap_samples, seed=cfg.seed
    )
    anomaly_effect = _anomaly_effect(matched_days, oy, anomaly_dates)

    p_value = stats.spearman_p_value(rho, n_eff)
    return {
        "pair": [pair.get("predictor"), pair.get("outcome")],
        "note": pair.get("note"),
        "lag": lag,
        "rho": round(rho, 4),
        "n": len(px),
        "n_eff": round(n_eff, 2),
        "p_value": round(p_value, 6),
        "ci": None if ci is None else [round(ci[0], 4), round(ci[1], 4)],
        "coverage": round(coverage, 4),
        "level": level,
        "missing_maybe_not_random": missing_flag,
        "missing_mean_diff": None if missing_diff is None else round(missing_diff, 4),
        "anomaly_day_effect": anomaly_effect,
    }


def _anomaly_effect(
    matched_days: list[str], outcome_values: list[float], anomaly_dates: set[str]
) -> dict[str, Any] | None:
    flagged = [
        v for d, v in zip(matched_days, outcome_values, strict=True) if d in anomaly_dates
    ]
    normal = [
        v for d, v in zip(matched_days, outcome_values, strict=True) if d not in anomaly_dates
    ]
    if not flagged or not normal:
        return None
    return {
        "anomaly_mean": round(stats.mean(flagged) or 0.0, 4),
        "other_mean": round(stats.mean(normal) or 0.0, 4),
        "anomaly_days": len(flagged),
    }


def _to_insight(index: int, cand: dict[str, Any], cfg: CorrelationConfig) -> dict[str, Any]:
    predictor, outcome = cand["pair"]
    return {
        "id": f"ins_{index:02d}",
        "pair": [predictor, outcome],
        "note": cand["note"],
        "lag_days": cand["lag"],
        "rho": cand["rho"],
        "n": cand["n"],
        "n_eff": cand["n_eff"],
        "ci": cand["ci"],
        "p_value": cand["p_value"],
        "level": cand["level"],
        "causal_claim": False,
        "missing_maybe_not_random": cand["missing_maybe_not_random"],
        "missing_mean_diff": cand["missing_mean_diff"],
        "anomaly_day_effect": cand["anomaly_day_effect"],
        "common_factors": [
            message("corr.common_factor.workload"),
            message("corr.common_factor.illness"),
            message("corr.common_factor.weekday"),
            message("corr.common_factor.season"),
        ],
        "uncontrolled_factors": [
            message("corr.uncontrolled.diet"),
            message("corr.uncontrolled.work_rhythm"),
            message("corr.uncontrolled.social"),
        ],
        "verification": message("corr.verification", predictor=predictor, outcome=outcome),
        "thresholds_ref": cfg.version,
        "lineage": {
            "source": message("corr.lineage_source"),
            "excluded": [],
            "corrections": [],
            "coverage": cand["coverage"],
        },
    }
