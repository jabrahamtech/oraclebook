"""Evaluation stats against hand-computed answers."""
import math
from itertools import pairwise
from statistics import NormalDist

import pytest

from oraclebook.analysis import (
    bootstrap_by_market,
    brier_score,
    calibration_bins,
    deflated_sharpe,
    expected_max_sharpe,
    kelly_fraction_binary,
    log_loss,
    probabilistic_sharpe,
    purged_walk_forward,
    sharpe,
)

PHI = NormalDist().cdf

# --- Sharpe family -----------------------------------------------------------------------


def test_sharpe_hand_computed():
    # mean 3, sample variance 2.5
    assert sharpe([1, 2, 3, 4, 5]) == pytest.approx(3 / math.sqrt(2.5))


def test_sharpe_rejects_degenerate_input():
    with pytest.raises(ValueError):
        sharpe([1.0])
    with pytest.raises(ValueError):
        sharpe([0.5, 0.5, 0.5])


def test_psr_symmetric_series_hand_computed():
    # [1..5]: SR = 3/sqrt(2.5), skew 0, m2 = 2, m4 = 6.8 -> kurtosis 1.7, SR^2 = 3.6
    # z = SR * sqrt(4) / sqrt(1 + 0.7/4 * 3.6) = 3.7947 / sqrt(1.63) = 2.97227
    assert probabilistic_sharpe([1, 2, 3, 4, 5]) == pytest.approx(PHI(2.972267), abs=1e-6)


def test_psr_skewed_series_with_benchmark_hand_computed():
    # [1,1,1,1,6]: mean 2, devs (-1,-1,-1,-1,4): m2 = 4, m3 = 12, m4 = 52
    # skew = 12 / 8 = 1.5, kurtosis = 52 / 16 = 3.25, SR = 2 / sqrt(5) = 0.894427
    # denom = 1 - 1.5*0.894427 + (2.25/4)*0.8 = 0.108359 -> sqrt 0.329179
    # z(SR* = 0.5) = (0.894427 - 0.5) * 2 / 0.329179 = 2.396424
    assert probabilistic_sharpe([1, 1, 1, 1, 6], sr_benchmark=0.5) == pytest.approx(
        PHI(2.396424), abs=1e-6
    )


def test_psr_is_half_when_sr_equals_benchmark():
    returns = [0.1, -0.05, 0.2, 0.0, 0.15, -0.1]
    assert probabilistic_sharpe(returns, sr_benchmark=sharpe(returns)) == pytest.approx(0.5)


def test_expected_max_sharpe_single_trial_is_zero():
    assert expected_max_sharpe(1, 0.25) == 0.0


def test_expected_max_sharpe_hand_computed():
    g = 0.5772156649015329
    inv = NormalDist().inv_cdf
    expected = 0.5 * ((1 - g) * inv(1 - 1 / 100) + g * inv(1 - 1 / (100 * math.e)))
    assert expected_max_sharpe(100, 0.25) == pytest.approx(expected)
    assert expected_max_sharpe(100, 0.25) == pytest.approx(1.2653, abs=1e-4)


def test_expected_max_sharpe_rejects_bad_input():
    with pytest.raises(ValueError):
        expected_max_sharpe(0, 0.1)
    with pytest.raises(ValueError):
        expected_max_sharpe(5, -0.1)


def test_deflated_sharpe_monotonic_decreasing_in_trials():
    returns = [0.02, -0.01, 0.03, 0.01, -0.005, 0.025, 0.0, 0.015, -0.02, 0.03] * 3
    values = [deflated_sharpe(returns, n, sr_variance=0.01) for n in (1, 2, 5, 10, 100, 1000)]
    assert values[0] == pytest.approx(probabilistic_sharpe(returns, 0.0))
    assert all(a > b for a, b in pairwise(values))


# --- bootstrap -----------------------------------------------------------------------------

PNL = {f"m{i}": v for i, v in enumerate([1.0, -0.5, 2.0, 0.3, -1.2, 0.8, 1.5, -0.2, 0.4, 0.9])}


def test_bootstrap_deterministic_under_seed():
    a = bootstrap_by_market(PNL, n_boot=2000, seed=7)
    b = bootstrap_by_market(PNL, n_boot=2000, seed=7)
    assert a == b
    reordered = dict(reversed(list(PNL.items())))
    assert bootstrap_by_market(reordered, n_boot=2000, seed=7) == a
    assert bootstrap_by_market(PNL, n_boot=2000, seed=8) != a


def test_bootstrap_ci_contains_mean():
    r = bootstrap_by_market(PNL, n_boot=4000)
    assert r.estimate == pytest.approx(sum(PNL.values()) / len(PNL))
    assert r.ci_low < r.estimate < r.ci_high
    assert 0.0 <= r.p_le_zero <= 1.0
    assert r.n_markets == 10


def test_bootstrap_sum_stat():
    r = bootstrap_by_market(PNL, n_boot=1000, stat="sum")
    assert r.estimate == pytest.approx(sum(PNL.values()))
    with pytest.raises(ValueError):
        bootstrap_by_market(PNL, stat="mode")


def test_bootstrap_identical_markets_gives_degenerate_ci():
    r = bootstrap_by_market({"a": 2.0, "b": 2.0, "c": 2.0}, n_boot=500)
    assert r.ci_low == r.ci_high == r.estimate == 2.0
    assert r.p_le_zero == 0.0


def test_bootstrap_resamples_markets_one_huge_market_widens_ci():
    base = bootstrap_by_market(PNL, n_boot=4000)
    with_whale = bootstrap_by_market({**PNL, "whale": 100.0}, n_boot=4000)
    assert (with_whale.ci_high - with_whale.ci_low) > 5 * (base.ci_high - base.ci_low)
    # The whale is one resampling unit: some draws omit it entirely, so the lower bound
    # stays near the no-whale mean instead of being dragged up to ~10.
    assert with_whale.ci_low < 2.0


def test_bootstrap_p_le_zero_for_losing_markets():
    r = bootstrap_by_market({"a": -1.0, "b": -2.0, "c": -0.5}, n_boot=500)
    assert r.p_le_zero == 1.0


# --- forecast quality -----------------------------------------------------------------------


def test_brier_known_values():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    # (0.2^2 + 0.6^2 + 0.3^2) / 3
    assert brier_score([0.8, 0.6, 0.3], [1, 0, 0]) == pytest.approx((0.04 + 0.36 + 0.09) / 3)


def test_log_loss_known_values():
    assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))
    assert log_loss([0.8, 0.6], [1, 0]) == pytest.approx(-(math.log(0.8) + math.log(0.4)) / 2)
    # A certain-and-wrong forecast is clipped, not infinite.
    assert log_loss([0.0], [1]) == pytest.approx(-math.log(1e-12))


def test_forecast_input_validation():
    with pytest.raises(ValueError):
        brier_score([0.5], [1, 0])
    with pytest.raises(ValueError):
        brier_score([1.5], [1])
    with pytest.raises(ValueError):
        log_loss([0.5], [2])


def test_calibration_bins_counts():
    probs = [0.05, 0.15, 0.12, 0.55, 0.95, 1.0, 0.0]
    outcomes = [0, 1, 0, 1, 1, 1, 0]
    bins = calibration_bins(probs, outcomes, n_bins=10)
    assert len(bins) == 10
    assert [b.n for b in bins] == [2, 2, 0, 0, 0, 1, 0, 0, 0, 2]
    assert bins[0].lo == 0.0 and bins[9].hi == 1.0
    assert bins[1].mean_prob == pytest.approx(0.135)
    assert bins[1].win_rate == pytest.approx(0.5)
    assert bins[2].mean_prob is None and bins[2].win_rate is None
    assert bins[9].mean_prob == pytest.approx(0.975)  # 1.0 lands in the last bin
    assert bins[9].win_rate == 1.0
    assert sum(b.n for b in bins) == len(probs)
    lo, hi, n, _mean_prob, win_rate = bins[5]
    assert (lo, hi, n, win_rate) == (0.5, 0.6, 1, 1.0)


# --- Kelly ----------------------------------------------------------------------------------


def test_kelly_buy_closed_form():
    # q = 0.6, c = 0.5: (0.6 - 0.5) / 0.5 = 0.2
    assert kelly_fraction_binary(0.6, 0.5) == pytest.approx(0.2)
    assert kelly_fraction_binary(0.6, 0.5, fraction=0.5) == pytest.approx(0.1)
    # fee 0.02 -> c_eff 0.52: 0.08 / 0.48
    assert kelly_fraction_binary(0.6, 0.5, fee_per_share=0.02) == pytest.approx(0.08 / 0.48)


def test_kelly_sell_symmetric():
    # SELL at 0.7 with q = 0.4 == buying NO at 0.3 with belief 0.6: (0.6 - 0.3) / 0.7
    assert kelly_fraction_binary(0.4, 0.7, side="SELL") == pytest.approx(0.3 / 0.7)
    assert kelly_fraction_binary(0.4, 0.7, side="SELL") == pytest.approx(
        kelly_fraction_binary(0.6, 0.3, side="BUY")
    )


def test_kelly_clamped_at_zero():
    assert kelly_fraction_binary(0.4, 0.5) == 0.0
    assert kelly_fraction_binary(0.6, 0.5, side="SELL") == 0.0
    assert kelly_fraction_binary(0.99, 0.95, fee_per_share=0.06) == 0.0  # c_eff >= 1
    with pytest.raises(ValueError):
        kelly_fraction_binary(0.5, 1.0)


# --- purged walk-forward --------------------------------------------------------------------


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start <= b_end and b_start <= a_end


def test_purged_walk_forward_no_leakage():
    # Mostly 10s-long markets every 10s, plus a couple of long-lived ones that straddle folds.
    items = [(f"k{i}", i * 10.0, i * 10.0 + 9.0) for i in range(20)]
    items += [("long_a", 15.0, 95.0), ("long_b", 55.0, 170.0)]
    spans = {k: (s, e) for k, s, e in items}
    embargo = 5.0
    splits = purged_walk_forward(items, n_folds=4, embargo_s=embargo)
    assert len(splits) == 4

    seen_test: list = []
    for train, test in splits:
        assert test and not set(train) & set(test)
        assert not set(test) & set(seen_test)  # test folds are disjoint
        seen_test += test
        t_lo = min(spans[k][0] for k in test)
        t_hi = max(spans[k][1] for k in test)
        for k in train:
            s, e = spans[k]
            assert not _overlaps(s, e, t_lo - embargo, t_hi + embargo), (k, test)
            assert s < t_lo  # walk-forward: training data starts before the test window

    # Test folds walk forward in time (ordered by start).
    starts = [min(spans[k][0] for k in test) for _, test in splits]
    assert starts == sorted(starts)
    # The straddling market is purged from the fold it overlaps.
    first_train, _ = splits[0]
    assert "long_a" not in first_train


def test_purged_walk_forward_embargo_purges_neighbours():
    items = [("a", 0.0, 9.0), ("b", 10.0, 19.0), ("c", 20.0, 29.0), ("d", 30.0, 39.0)]
    no_embargo = purged_walk_forward(items, n_folds=1, embargo_s=0.0)
    assert no_embargo == [(["a", "b"], ["c", "d"])]
    with_embargo = purged_walk_forward(items, n_folds=1, embargo_s=2.0)
    assert with_embargo == [(["a"], ["c", "d"])]  # b ends at 19, within 2s of c's start


def test_purged_walk_forward_validation():
    with pytest.raises(ValueError):
        purged_walk_forward([("a", 0.0, 1.0)], n_folds=1, embargo_s=0.0)
    with pytest.raises(ValueError):
        purged_walk_forward([("a", 2.0, 1.0), ("b", 3.0, 4.0)], n_folds=1, embargo_s=0.0)
