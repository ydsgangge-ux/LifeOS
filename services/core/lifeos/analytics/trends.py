"""趋势计算（第 8.4 节）。

- 周对周变化、近 4 周滚动均值。
- 线性回归斜率只在样本数不少于 6 时报告，并给置信区间；本例按周序列，
  周数不足 3 时不给方向。
- v1 不处理季节性。
"""

from __future__ import annotations

from datetime import date, timedelta

from . import stats
from .metrics import MetricEngine

FLAT_SLOPE_RATIO = 0.05  # 每周斜率绝对值低于均值的 5% 视为「不动」
REGRESSION_MIN_N = 6     # 第 8.4 节：n ≥ 6 才报告回归斜率


def weekly_series(
    engine: MetricEngine,
    key: str,
    start: date,
    end: date,
    *,
    agg: str = "mean",
) -> list[dict[str, object]]:
    """把日粒度值按自然周（周一起）聚合。"""
    daily = engine.daily_values(key, start, end)
    buckets: dict[str, list[float]] = {}
    for date_str, value in daily.items():
        d = date.fromisoformat(date_str)
        monday = d - timedelta(days=d.weekday())
        buckets.setdefault(monday.isoformat(), []).append(value)
    out: list[dict[str, object]] = []
    for week_start in sorted(buckets):
        values = buckets[week_start]
        value = sum(values) if agg == "sum" else (sum(values) / len(values))
        out.append({"week_start": week_start, "value": value, "n": len(values)})
    return out


def regression(series: list[dict[str, object]]) -> dict[str, float] | None:
    if len(series) < REGRESSION_MIN_N:
        return None
    xs = [float(i) for i in range(len(series))]
    ys = [float(v["value"]) for v in series]  # type: ignore[arg-type]
    return stats.linear_slope(xs, ys)


def rolling_mean(series: list[dict[str, object]], window: int = 4) -> float | None:
    if not series:
        return None
    tail = [float(v["value"]) for v in series[-window:]]  # type: ignore[arg-type]
    return stats.mean(tail)


def trend_direction(series: list[dict[str, object]]) -> str | None:
    """返回 up / flat / down；无数据返回 None。"""
    if not series:
        return None
    ys = [float(v["value"]) for v in series]  # type: ignore[arg-type]
    if len(ys) >= 3:
        reg = stats.linear_slope([float(i) for i in range(len(ys))], ys)
        slope = None if reg is None else reg["slope"]
    else:
        slope = ys[-1] - ys[0]
    if slope is None:
        return "flat"
    scale = max(abs(stats.mean(ys) or 0.0), 1e-9)
    if slope > FLAT_SLOPE_RATIO * scale:
        return "up"
    if slope < -FLAT_SLOPE_RATIO * scale:
        return "down"
    return "flat"


def non_increasing_run(series: list[dict[str, object]]) -> int:
    """从最近一周往前数，连续「未上升」（当周 ≤ 前一周）的周数。"""
    run = 0
    for i in range(len(series) - 1, 0, -1):
        cur = float(series[i]["value"])  # type: ignore[arg-type]
        prev = float(series[i - 1]["value"])  # type: ignore[arg-type]
        if cur <= prev:
            run += 1
        else:
            break
    return run


def near_zero_run(series: list[dict[str, object]], threshold: float = 0.0) -> int:
    """从最近一周往前数，连续「接近 0」（当周 ≤ 阈值）的周数。"""
    run = 0
    for i in range(len(series) - 1, -1, -1):
        if float(series[i]["value"]) <= threshold:  # type: ignore[arg-type]
            run += 1
        else:
            break
    return run
