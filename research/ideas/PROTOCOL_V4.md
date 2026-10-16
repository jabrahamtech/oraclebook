# Protocol v4: machine learning, a time-series foundation model, and bull regimes on crypto
ladders (written before the data was built or looked at)

Rounds 1–3 found no taker edge from hand-built models. This round asks whether methods retail
rarely brings to Kalshi do better, and whether anything works specifically in bull markets.

## Data

`cx.py`: Kalshi's daily BTC and ETH "above K" ladders closing 17:00 ET (the only hour listed
since Nov 2024), **2024-11-01 → 2026-10-14** (markets settled before Kalshi's 2026-08-15 cutoff
from `/historical/*`), strikes within ±2% of the Binance price 60 min before the close
(outcome-blind; the first 50 BTC days were fetched at ±4% and are trimmed to ±2% when the dataset
is built), their trades in the last 65 min; Binance 1-minute spot. One row per market and
decision time t ∈ {60, 30, 15, 5} min before the close; features only from data before t.

## Split

Exploration: events before **2026-01-01** (covers the Nov–Dec 2024 bull run and 2025).
Holdout: 2026-01-01 → 2026-10-14, touched once per frozen rule.

## Tests

- **G1 – gradient boosting** (scikit-learn HistGradientBoostingClassifier): features = distance
  to strike in σ (z), minutes left, Binance returns (5 m, 15 m, 60 m, 1 d, 7 d, 30 d), realised
  volatility (60 m, 1 d), taker flow imbalance in the last 15 min, and the market's last trade.
  Exploration: walk-forward, each month predicted by a model fitted on all earlier months. The
  rule buys the side whose model probability exceeds the ask + fee by more than e. Holdout: one
  model fitted on all of exploration.
- **F1 – time-series foundation model** (Amazon Chronos-Bolt, zero-shot, released Nov 2024 so
  every test date is after its release): quantile forecast of the price at the close from the
  past minutes → P(above K); same comparison and rule as G1. Run only if the model can be
  installed and run locally.
- **B1 – bull regimes:** in events where BTC's (or ETH's) 30-day return before t exceeds +10%,
  is YES on near-the-money strikes (|z| < 1) underpriced? Calibration of the market by regime,
  and the rule "buy YES at the ask" in bull regimes, net of fees.
- LLM forecasters (Jev-style, OpenForecaster): **not backtestable** — their training data can
  contain the outcomes. Only a forward log of their probabilities on open markets is valid;
  out of scope until an API key or a local runtime is available.

## Costs and judgement

Taker at the last print in the 15 min before t where a taker bought that side (an ask that
existed), fee 0.07·p·(1−p); held to settlement; dollar-weighted per $1; CI resampling events.
Brier of market (last trade) vs model reported for every test. Frozen rules (CANDIDATES_V4.md)
pass at α = 0.05 / (number of frozen rules), CI above zero, ≥ 200 trades over ≥ 20 events, same
sign as exploration. Nothing here can show a strategy is profitable.

### Amendment (before running it): F1 on the round-2 15-minute markets

While the ladder history downloads, F1 is also run on round 2's sampled `KXBTC15M` / `KXETH15M`
tapes (2026-08-21 → 10-14, split 2026-09-25 as in round 2): at 10 and 5 min before the close,
Chronos-Bolt's forecast of the price at the close (from the last 512 Binance minutes) gives
P(close ≥ K*), K* being the market's strike (the price 15 minutes earlier). Same Brier comparison
and taker rule (ask = last taker-buy print in the previous 2 min). Exploratory only unless a rule
is frozen in CANDIDATES_V4.md before its holdout run.
