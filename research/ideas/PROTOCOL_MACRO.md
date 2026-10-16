# Protocol: public macro nowcasts vs Kalshi macro markets (written before any outcome was compared)

Question: do free, as-of macro nowcasts know something Kalshi's macro brackets don't, the day
before the release? Rounds 1–3 found every public model behind the market (weather, crypto); this
checks the macro family before anyone builds on the idea.

## Data

- Kalshi CPI month-over-month brackets (`KXCPI`, incl. the older `CPI-` events the series serves):
  every settled market 2022-01 → 2026-10, trades in the last 48 h before close; markets settled
  before Kalshi's historical cutoff (2026-08-15) come from `/historical/*`
  (`research/data/kalshi_api.py --historical`). Same for `KXGDP` (secondary).
- Cleveland Fed inflation nowcast (`research/data/macro_sources.py`): one chart per target month
  with the daily headline CPI MoM nowcast as it stood each day, and the actual print.
- Atlanta Fed GDPNow tracking archive (daily as-of estimates), secondary.

## Model (fixed now)

- Information time τ = market close − 24 h (CPI markets close minutes before the 08:30 ET
  release). Nowcast used: the latest daily value dated on a calendar day **before** τ's date
  (Cleveland updates intraday; a full day's lag removes any same-day look-ahead).
- CPI is published to 0.1 pp. A contract "above f" pays if the published MoM > f, i.e. the
  unrounded change ≥ f + 0.05. Model: unrounded ~ Normal(nowcast + b, s), b and s the mean and SD
  of (actual − nowcast at the same lead) over target months **2014-01 → 2021-12**, which precede
  every Kalshi market, so calibration uses no Kalshi-period outcome.

## Split, costs, judgement

- Exploration: events closing before 2025-01-01. Holdout: 2025-01-01 → 2026-10-14, touched once
  per frozen rule.
- Market price at τ: the last print in the 6 h before τ in which a taker bought that side (an
  ask that existed). A taker buys the side whose model probability exceeds that ask plus the fee
  0.07·p·(1−p) by more than an edge e (chosen on exploration); held to settlement.
- Reported always: Brier score of market (last trade before τ) vs nowcast model, per split.
- Frozen rules (logged in CANDIDATES_MACRO.md before their holdout run) pass at α = 0.05 / (number
  of frozen rules) with the event-clustered CI above zero and the same sign as exploration. With
  ~20 holdout events the test has little power; a pass would only justify a forward paper test.

Nothing here can show a strategy is profitable.
