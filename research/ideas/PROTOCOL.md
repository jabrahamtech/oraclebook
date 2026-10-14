# Idea-testing protocol (written before any idea was tested)

Goal: find out whether any simple, mechanical idea shows an edge on these historical samples
that survives costs and an untouched holdout. This is research; a surviving idea is a lead worth a
forward (paper) test, not evidence that a strategy is profitable.

## Data and split

Panels from `research/data/` (six venues/sets) plus Binance 1-minute klines of the underlying
coins (`research/data/raw/binance_klines_1m`, checksum-verified) and the Coinbase BTC 1-minute
candles shipped with the Kalshi source.

Each dataset is split **by time**: markets are ordered by scheduled close; the earliest 60% are
the *exploration* set, the latest 40% the *holdout*. Every idea is developed, tuned and selected
on exploration only. Each idea gets **one** frozen rule (thresholds, horizon, side) chosen on
exploration; only that rule is run on the holdout, once. Ideas whose exploration result is not
positive after costs are not taken to the holdout at all.

## Costs (always applied)

- Taker: buy at the best ask; Polymarket fee 0.07·p·(1−p) per contract (conservative; many
  markets charge less or nothing), Kalshi fee ceil-to-cent of 0.07·p·(1−p).
- Maker: no fee; a resting order fills only when the market trades through it (trade tape) or,
  with quotes only, when the opposite best quote crosses it (the conservative trade-through rule
  the Replay paper executor uses). Adverse selection is therefore included.
- No size beyond the displayed top-of-book size where sizes exist.

## Decision rule

An idea is "worth pursuing" only if, on the holdout:
1. return per $1 net of costs has a 95% bootstrap CI that excludes zero on the positive side,
   resampling the *coarser* cluster (event / UTC day), Bonferroni-adjusted for the number of
   rules taken to the holdout;
2. at least 200 trades over at least 20 clusters;
3. the sign matches exploration.

Everything tried is listed in the results, including failures and the count of rules tested.
