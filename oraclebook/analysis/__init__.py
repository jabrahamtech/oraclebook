"""Quant evaluation for Replay / paper / live results — markouts and research statistics.

Standard library only (no numpy/scipy), so it adds no runtime dependency. Everything here
reports numbers; none of it is a claim that a strategy is profitable.
"""
from oraclebook.analysis.markouts import (
    DEFAULT_HORIZONS,
    FillMarkout,
    MarkoutSummary,
    markouts,
    mid_series_from_recording,
    summarize,
)
from oraclebook.analysis.stats import (
    BootstrapResult,
    CalibrationBin,
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

__all__ = [
    "DEFAULT_HORIZONS",
    "BootstrapResult",
    "CalibrationBin",
    "FillMarkout",
    "MarkoutSummary",
    "bootstrap_by_market",
    "brier_score",
    "calibration_bins",
    "deflated_sharpe",
    "expected_max_sharpe",
    "kelly_fraction_binary",
    "log_loss",
    "markouts",
    "mid_series_from_recording",
    "probabilistic_sharpe",
    "purged_walk_forward",
    "sharpe",
    "summarize",
]
