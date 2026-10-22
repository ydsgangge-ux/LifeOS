"""确定性统计工具（第 8.4 / 8.5 节）。

只用标准库：不依赖 scipy / numpy，保证任何平台与任何运行次数下结果一致。
包含：均值、样本标准差、线性回归斜率与置信区间、Spearman 相关、一阶自相关、
以及自实现的精确 t 分布（正则化不完全 beta，Numerical Recipes 的 betacf/betai）。
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence

# ---------------------------------------------------------------------------
# 基础统计量
# ---------------------------------------------------------------------------


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def sample_std(values: Sequence[float]) -> float | None:
    """样本标准差（ddof=1）。少于 2 个样本返回 None。"""
    n = len(values)
    if n < 2:
        return None
    mu = sum(values) / n
    var = sum((v - mu) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def ranks(values: Sequence[float]) -> list[float]:
    """平均秩（并列取平均），用于 Spearman。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # 秩从 1 开始
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return pearson(ranks(xs), ranks(ys))


def autocorr_lag1(values: Sequence[float]) -> float:
    """一阶自相关；样本不足或方差为 0 时返回 0。"""
    if len(values) < 3:
        return 0.0
    r = pearson(values[:-1], values[1:])
    return 0.0 if r is None else r


# ---------------------------------------------------------------------------
# 线性回归（第 8.4 节：n ≥ 6 才报告斜率，并给置信区间）
# ---------------------------------------------------------------------------


def linear_slope(
    xs: Sequence[float], ys: Sequence[float], *, confidence: float = 0.95
) -> dict[str, float] | None:
    """最小二乘斜率与置信区间。样本少于 3 或 x 无变化时返回 None。"""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((a - mx) ** 2 for a in xs)
    if sxx <= 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = [b - (intercept + slope * a) for a, b in zip(xs, ys, strict=True)]
    dof = n - 2
    sse = sum(r * r for r in resid)
    if dof <= 0:
        return {"slope": slope, "intercept": intercept, "n": float(n)}
    se = math.sqrt(sse / dof / sxx)
    tcrit = t_ppf(1 - (1 - confidence) / 2, dof)
    return {
        "slope": slope,
        "intercept": intercept,
        "n": float(n),
        "se": se,
        "ci_low": slope - tcrit * se,
        "ci_high": slope + tcrit * se,
    }


# ---------------------------------------------------------------------------
# t 分布（精确：正则化不完全 beta）
# ---------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """连分式展开（Numerical Recipes betacf）。"""
    max_iter = 200
    eps = 3e-12
    fpmin = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """正则化不完全 beta 函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t: float, dof: float) -> float:
    """t 分布累积分布函数。"""
    x = dof / (dof + t * t)
    ib = betai(dof / 2.0, 0.5, x)
    return 1.0 - 0.5 * ib if t > 0 else 0.5 * ib


def student_t_p_two_sided(t: float, dof: float) -> float:
    """双侧 p 值：2 × P(T > |t|)。"""
    if dof <= 0:
        return 1.0
    x = dof / (dof + t * t)
    return betai(dof / 2.0, 0.5, x)


def t_ppf(p: float, dof: float) -> float:
    """t 分布分位点（二分法，足够精确）。p 为累积概率。"""
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")
    if p < 0.5:
        return -t_ppf(1.0 - p, dof)
    lo, hi = 0.0, 1e6
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if student_t_cdf(mid, dof) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def spearman_p_value(rho: float, n_eff: float) -> float:
    """用 t 近似（df = n_eff − 2）给出 Spearman 的双侧 p 值。"""
    dof = n_eff - 2.0
    if dof <= 0 or abs(rho) >= 1.0:
        return 1.0
    t = rho * math.sqrt(dof / (1.0 - rho * rho))
    return student_t_p_two_sided(t, dof)


# ---------------------------------------------------------------------------
# 多重比较（第 8.5 节：Benjamini-Hochberg，控制 FDR）
# ---------------------------------------------------------------------------


def benjamini_hochberg(p_values: Sequence[float], q: float) -> list[bool]:
    """返回每个 p 值是否通过 BH 控制（拒绝原假设）。保持输入顺序。"""
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    passed = [False] * m
    threshold_rank = -1
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= (rank / m) * q:
            threshold_rank = rank
    if threshold_rank >= 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= threshold_rank:
                passed[idx] = True
    return passed


# ---------------------------------------------------------------------------
# 块 bootstrap（第 8.5 节：7 天一块；固定随机种子保证确定性）
# ---------------------------------------------------------------------------


def block_bootstrap_ci(
    xs: Sequence[float],
    ys: Sequence[float],
    *,
    block_days: int,
    samples: int,
    seed: int,
    confidence: float = 0.95,
) -> tuple[float, float] | None:
    """按块重采样 n 对样本，返回 Spearman 的百分位置信区间。"""
    n = len(xs)
    if n < block_days or n < 3 or n != len(ys):
        return None
    rng = random.Random(seed)
    n_blocks = n // block_days
    if n_blocks < 2:
        return None
    starts_pool = list(range(0, n - block_days + 1))
    rhos: list[float] = []
    for _ in range(samples):
        bx: list[float] = []
        by: list[float] = []
        for _ in range(n_blocks):
            s = rng.choice(starts_pool)
            bx.extend(xs[s : s + block_days])
            by.extend(ys[s : s + block_days])
        r = spearman(bx, by)
        if r is not None:
            rhos.append(r)
    if len(rhos) < 10:
        return None
    rhos.sort()
    lo_idx = int((1 - confidence) / 2 * len(rhos))
    hi_idx = int((1 + confidence) / 2 * len(rhos)) - 1
    lo_idx = max(0, min(lo_idx, len(rhos) - 1))
    hi_idx = max(0, min(hi_idx, len(rhos) - 1))
    return rhos[lo_idx], rhos[hi_idx]


def effective_sample_size(xs: Sequence[float], ys: Sequence[float]) -> float:
    """按两个序列的一阶自相关估计有效样本数 n_eff（第 8.5 节）。

    n_eff = n × (1 − r1x·r1y) ÷ (1 + r1x·r1y)，截断到 [2, n]。
    """
    n = len(xs)
    if n < 3:
        return float(n)
    r1x = autocorr_lag1(xs)
    r1y = autocorr_lag1(ys)
    denom = 1.0 + r1x * r1y
    if denom <= 0:
        return float(n)
    n_eff = n * (1.0 - r1x * r1y) / denom
    return max(2.0, min(float(n), n_eff))


def remove_weekday_effect(
    by_date: dict[str, float], weekday_of: Callable[[str], int]
) -> dict[str, float]:
    """每个指标减去自己的星期均值（去掉星期效应）。

    weekday_of 是「ISO 日期字符串 → 0..6」的可调用对象（见 periods.weekday_of）。
    """
    buckets: dict[int, list[float]] = {}
    for date_key, value in by_date.items():
        wd = weekday_of(date_key)
        buckets.setdefault(wd, []).append(value)
    means = {wd: sum(vals) / len(vals) for wd, vals in buckets.items()}
    return {k: v - means[weekday_of(k)] for k, v in by_date.items()}
