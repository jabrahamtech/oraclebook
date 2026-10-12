"""Evaluation statistics for strategy research — standard library only.

Sharpe-family functions work on a per-observation return series and are **not
annualized**: `sharpe` is mean / sample stdev of the observations as given (per trade, per
market, per day — whatever the series is), and the probabilistic / deflated variants compare
against benchmarks expressed in those same per-observation units.

References:
    Bailey & López de Prado (2012), "The Sharpe Ratio Efficient Frontier" (PSR).
    Bailey & López de Prado (2014), "The Deflated Sharpe Ratio" (DSR, expected max SR).
    López de Prado (2018), "Advances in Financial Machine Learning", ch. 7 (purging).
"""
from __future__ import annotations

import math
import random
import statistics
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import NamedTuple

EULER_MASCHERONI = 0.5772156649015329

_STD_NORMAL = NormalDist()


# --- Sharpe family -------------------------------------------------------------------------


def sharpe(returns: Sequence[float]) -> float:
    """Per-observation Sharpe ratio: mean / sample stdev (ddof=1). Not annualized."""
    if len(returns) < 2:
        raise ValueError("sharpe needs at least 2 observations")
    sd = statistics.stdev(returns)
    if sd == 0.0:
        raise ValueError("sharpe is undefined for a zero-variance series")
    return statistics.fmean(returns) / sd


def _skew_kurtosis(returns: Sequence[float]) -> tuple[float, float]:
    """Moment-based sample skewness and non-excess kurtosis (normal -> 0, 3)."""
    n = len(returns)
    mu = statistics.fmean(returns)
    m2 = sum((x - mu) ** 2 for x in returns) / n
    m3 = sum((x - mu) ** 3 for x in returns) / n
    m4 = sum((x - mu) ** 4 for x in returns) / n
    return m3 / m2**1.5, m4 / m2**2


def probabilistic_sharpe(returns: Sequence[float], sr_benchmark: float = 0.0) -> float:
    """Probability that the true per-observation SR exceeds `sr_benchmark` (PSR).

        PSR = Φ( (SR̂ − SR*)·√(n−1) / √(1 − γ3·SR̂ + (γ4 − 1)/4 · SR̂²) )

    with γ3 the sample skewness and γ4 the non-excess sample kurtosis.
    """
    n = len(returns)
    sr = sharpe(returns)
    skew, kurt = _skew_kurtosis(returns)
    denom_sq = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if denom_sq <= 0.0:
        raise ValueError("PSR variance term is non-positive for this return series")
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom_sq)
    return _STD_NORMAL.cdf(z)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum SR across `n_trials` independent zero-skill trials.

        E[max SR] ≈ √V · ((1 − γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)))

    `sr_variance` (V) is the variance of the SR estimates across the trials, in the same
    per-observation units. A single trial has no selection bias, so N = 1 returns 0.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if sr_variance < 0.0:
        raise ValueError("sr_variance must be non-negative")
    if n_trials == 1:
        return 0.0
    g = EULER_MASCHERONI
    inv = _STD_NORMAL.inv_cdf
    return math.sqrt(sr_variance) * (
        (1.0 - g) * inv(1.0 - 1.0 / n_trials) + g * inv(1.0 - 1.0 / (n_trials * math.e))
    )


def deflated_sharpe(returns: Sequence[float], n_trials: int, sr_variance: float) -> float:
    """PSR against the expected max SR of `n_trials` trials (selection-bias adjusted)."""
    return probabilistic_sharpe(returns, expected_max_sharpe(n_trials, sr_variance))


# --- bootstrap ------------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapResult:
    estimate: float
    ci_low: float
    ci_high: float
    p_le_zero: float
    n_markets: int
    n_boot: int


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile of an already sorted sequence."""
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


_STATS = {"mean": statistics.fmean, "sum": math.fsum, "median": statistics.median}


def bootstrap_by_market(
    pnl_by_market: dict[str, float],
    n_boot: int = 10_000,
    ci: float = 0.95,
    seed: int = 0,
    stat: str = "mean",
) -> BootstrapResult:
    """Percentile bootstrap that resamples **markets** (not trades) with replacement.

    Trades within one market share an outcome and are strongly dependent, so the market is
    the independent unit. `stat` is "mean", "sum" or "median" of per-market PnL. Returns the
    point estimate, the percentile CI, and the share of bootstrap draws with stat <= 0.
    Deterministic for a given `seed` (markets are ordered by key before resampling).
    """
    if stat not in _STATS:
        raise ValueError(f"stat must be one of {sorted(_STATS)}, got {stat!r}")
    if not pnl_by_market:
        raise ValueError("bootstrap_by_market needs at least one market")
    if not 0.0 < ci < 1.0:
        raise ValueError("ci must be in (0, 1)")
    if n_boot < 1:
        raise ValueError("n_boot must be >= 1")
    fn = _STATS[stat]
    values = [float(pnl_by_market[k]) for k in sorted(pnl_by_market)]
    n = len(values)
    rng = random.Random(seed)
    draws = sorted(fn(rng.choices(values, k=n)) for _ in range(n_boot))
    alpha = 1.0 - ci
    return BootstrapResult(
        estimate=fn(values),
        ci_low=_quantile(draws, alpha / 2.0),
        ci_high=_quantile(draws, 1.0 - alpha / 2.0),
        p_le_zero=sum(1 for d in draws if d <= 0.0) / n_boot,
        n_markets=n,
        n_boot=n_boot,
    )


# --- forecast quality -----------------------------------------------------------------------


def _check_forecasts(probs: Sequence[float], outcomes: Sequence[int]) -> None:
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have the same length")
    if not probs:
        raise ValueError("need at least one forecast")
    if any(not 0.0 <= p <= 1.0 for p in probs):
        raise ValueError("probabilities must be in [0, 1]")
    if any(o not in (0, 1) for o in outcomes):
        raise ValueError("outcomes must be 0 or 1")


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Mean squared error of probability forecasts against 0/1 outcomes."""
    _check_forecasts(probs, outcomes)
    return statistics.fmean((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True))


def log_loss(probs: Sequence[float], outcomes: Sequence[int], eps: float = 1e-12) -> float:
    """Mean negative log-likelihood (natural log); probabilities clipped to [eps, 1 − eps]."""
    _check_forecasts(probs, outcomes)
    total = 0.0
    for p, o in zip(probs, outcomes, strict=True):
        p = min(max(p, eps), 1.0 - eps)
        total -= math.log(p) if o == 1 else math.log(1.0 - p)
    return total / len(probs)


class CalibrationBin(NamedTuple):
    lo: float
    hi: float
    n: int
    mean_prob: float | None
    win_rate: float | None


def calibration_bins(
    probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10
) -> list[CalibrationBin]:
    """Equal-width bins on [0, 1]; each is [lo, hi) except the last, which includes 1.0.

    Empty bins are kept (n = 0, mean_prob / win_rate None) so the output shape is fixed.
    """
    _check_forecasts(probs, outcomes)
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes, strict=True):
        buckets[min(int(p * n_bins), n_bins - 1)].append((p, o))
    bins = []
    for i, bucket in enumerate(buckets):
        n = len(bucket)
        bins.append(
            CalibrationBin(
                lo=i / n_bins,
                hi=(i + 1) / n_bins,
                n=n,
                mean_prob=statistics.fmean(p for p, _ in bucket) if n else None,
                win_rate=sum(o for _, o in bucket) / n if n else None,
            )
        )
    return bins


# --- sizing ---------------------------------------------------------------------------------


def kelly_fraction_binary(
    p_model: float,
    price: float,
    side: str = "BUY",
    fraction: float = 1.0,
    fee_per_share: float = 0.0,
) -> float:
    """Kelly bankroll fraction for a binary contract paying 1.

    BUY (YES at price c, belief q): f* = (q − c_eff) / (1 − c_eff), c_eff = c + fee.
    SELL (equivalently buying NO at 1 − c, belief 1 − q): f* = ((1 − q) − c_eff) / (1 − c_eff),
    c_eff = (1 − c) + fee. Clamped at 0 (no edge, no bet) and scaled by `fraction`
    (e.g. 0.5 for half-Kelly).
    """
    if not 0.0 <= p_model <= 1.0:
        raise ValueError("p_model must be in [0, 1]")
    if not 0.0 < price < 1.0:
        raise ValueError("price must be in (0, 1)")
    if fraction < 0.0 or fee_per_share < 0.0:
        raise ValueError("fraction and fee_per_share must be non-negative")
    side = side.upper()
    if side == "BUY":
        q, cost = p_model, price
    elif side == "SELL":
        q, cost = 1.0 - p_model, 1.0 - price
    else:
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    c_eff = cost + fee_per_share
    if c_eff >= 1.0:
        return 0.0
    return max(0.0, (q - c_eff) / (1.0 - c_eff)) * fraction


# --- cross-validation -----------------------------------------------------------------------


def purged_walk_forward(
    items: Sequence[tuple[Hashable, float, float]], n_folds: int, embargo_s: float
) -> list[tuple[list[Hashable], list[Hashable]]]:
    """Walk-forward splits with López de Prado purging and an embargo.

    `items` are `(key, start_ts, end_ts)` — e.g. one per market, spanning first fill to
    resolution. Items are ordered by `(start_ts, end_ts)` and cut into `n_folds + 1`
    contiguous blocks; fold k tests block k (k = 1..n_folds), so test folds are contiguous
    in time and walk forward. Training uses only earlier blocks (expanding window), and any
    item whose [start, end] overlaps the test window [first start − embargo, last end +
    embargo] is purged, so no label in train is resolved by information inside the test span.
    """
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    if embargo_s < 0.0:
        raise ValueError("embargo_s must be non-negative")
    if len(items) < n_folds + 1:
        raise ValueError("need at least n_folds + 1 items")
    for key, start, end in items:
        if end < start:
            raise ValueError(f"item {key!r} ends before it starts")
    ordered = sorted(items, key=lambda it: (it[1], it[2]))
    n_blocks = n_folds + 1
    base, extra = divmod(len(ordered), n_blocks)
    blocks: list[list[tuple[Hashable, float, float]]] = []
    i = 0
    for b in range(n_blocks):
        size = base + (1 if b < extra else 0)
        blocks.append(ordered[i : i + size])
        i += size

    splits: list[tuple[list[Hashable], list[Hashable]]] = []
    for k in range(1, n_blocks):
        test = blocks[k]
        lo = min(s for _, s, _ in test) - embargo_s
        hi = max(e for _, _, e in test) + embargo_s
        train = [
            key
            for block in blocks[:k]
            for key, start, end in block
            if end < lo or start > hi
        ]
        splits.append((train, [key for key, _, _ in test]))
    return splits
