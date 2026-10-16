# Round 4 frozen rules (PROTOCOL_V4.md; appended when chosen on exploration)

All round-4 holdouts are judged at α = 0.05 / 4 = 0.0125 (98.75% CI), fixed now for the four
rules this round can freeze (G1, F1, B1, F15), whether or not all of them are frozen.

| id | idea | frozen rule | exploration |
|---|---|---|---|
| F15 | Chronos-Bolt (small, zero-shot) vs 15-minute BTC/ETH up/down | cx15.py: at 10 and 5 min before the close, P(Binance at close ≥ Binance in the minute before the open) from a normal fitted to Chronos' 10/50/90% quantiles of the price at the close (context: last 512 one-minute closes); buy the side whose probability exceeds the ask (last taker-buy print in the previous 2 min) + fee by more than **0.05**; 1 contract; CI by event | round-2 sample, closes before 2026-09-25: Brier market vs Chronos — BTC 5 min 0.1277 / 0.1316, 10 min 0.1863 / 0.1881; ETH 5 min 0.1116 / 0.1133, 10 min 0.1842 / 0.1911; 50/50 blend better than the market in 3 of 4. Rule: edge 0 +1.81% [−4.02, +7.67] (938 trades) / 0.03 +5.81% / **0.05 +6.73% [−2.96, +16.54] (506 trades, 360 events)** / 0.10 +9.27% |
| G1 | gradient boosting (scikit-learn HistGradientBoosting, settings in cx_models.gbm) on Binance features + market state | **BTC only** (ETH still downloading when frozen); one model fitted on all BTC exploration rows (Nov 2024 → Dec 2025); at t ∈ {60, 30, 15, 5} min before the 17:00 ET close, buy the side whose model probability exceeds the ask + fee by more than **0.05**, the ask being a taker-buy print **at most 60 s old**; 1 contract; all regimes; CI by event | BTC walk-forward (each month predicted by earlier months), after fixing a 1-minute look-ahead in the Binance features: Brier model 0.0503 vs market 0.0472 (the model alone is worse); rule +5.89% [−0.32, +12.01], 767 trades, 287 events. Same rule with asks ≤ 30 s: +8.06% [+1.41, +14.81]; outside bull regimes (30-day return ≤ +10%): +9.11% [+2.63, +15.66]; inside: negative. Threshold 0: +2.62% [−0.51, +5.71] |
| B1 | bull regimes (BTC 30-day return > +10%), near-the-money strikes | — | buy YES: −10.91% [−23.35, +1.34]; buy NO: −1.15% to −12.06% (all CIs span 0). The market's mid sits above the YES rate in bull runs (0.554 vs 0.527), but the spread and fees absorb it in both directions → **nothing frozen** |
| F1 | Chronos-Bolt zero-shot on the daily ladders | — | Brier model 0.0526 vs market 0.0487; taker rule −0.2% to −3.3% overall → **not frozen** |

## Holdout (each run once, α = 0.0125)

| id | holdout | passes? |
|---|---|---|
| F15 | −8.62% [−28.47, +12.83], 252 trades, 172 events (closes 2026-09-25 → 10-14) | **no** — sign flipped; the exploration trend in the edge threshold did not carry over |
| G1 | −1.05% [−8.63, +6.70], 1,186 trades, 276 BTC events (2026-01-01 → 10-14) | **no** — the walk-forward gain in exploration did not carry over |
