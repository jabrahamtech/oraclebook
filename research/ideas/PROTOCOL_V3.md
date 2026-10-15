# Protocol v3: weather markets after the observations are in (written before analysis)

Rounds 1–2 ([RESULTS.md](RESULTS.md), [RESULTS_V2.md](RESULTS_V2.md)) found no taker edge from
forecasts, flow or fair-value models. Round 3 asks a narrower question with a mechanical answer:
**do Kalshi daily-high markets keep trading below their settled value after the airport's own
observations have already decided them?**

## Data

- Kalshi daily-high markets, 20 cities, every settled market closing 2026-08-15 → 2026-10-14,
  trades **12 → 0 h before close** (≈ 13:00 local standard time to the end of the climate day),
  public API, `research/data/raw/weather_pm/` (new pull; rounds 1–2 never saw it).
- METARs for each settlement station from the Iowa Environmental Mesonet archive
  (`research/data/iem_asos.py`). The station map (`wxobs.STATIONS`) was checked against the
  settled highs only (not prices): the day's METAR maximum (0.1 °C T-group and 6-hour max
  groups, rounded to whole °F) equals Kalshi's `expiration_value` on 98% of 1,220 city-days and
  exceeds it on 3.

## What a trader knows

At time τ, the known lower bound of the day's high is L(τ) = round(max of METAR temperatures
and 6-hour maxima of that climate day valid by τ − 10 min). A contract is **decided** at τ when,
with a margin δ °F:
- `greater` (YES iff high > f): L ≥ f + 1 + δ → YES;
- `less` (YES iff high < c): L ≥ c + δ → NO;
- `between` [a, b]: L ≥ b + 1 + δ → NO.

## Split, costs, judgement

Same split as round 2 by close time: exploration before 2026-09-25, holdout after; the holdout is
touched once per frozen rule. A taker buys the decided side only at the price of a trade print in
which a taker bought that side after τ (i.e. an ask that existed), paying 0.07·p·(1−p) per
contract; one entry per print, size capped at the printed size. Returns are dollar-weighted per
$1, CI by resampling events (city-days). Frozen rules (logged in CANDIDATES_V3.md before their
holdout run) pass at α = 0.05 / (number of frozen rules) with the CI above zero, ≥ 200 trades over
≥ 20 events, same sign as exploration.

## Also in this round (exploratory, no holdout claim)

- Live order books: a forward scan of every open Kalshi event for structural arbitrage — bracket
  sets whose YES asks sum below $1 (or bids above $1) net of fees, and ordered strikes whose
  prices violate monotonicity. Reported as counts, sizes and durations only.
- "Peak already passed": late in the day, the probability that the high stays at L given the hour
  and how far the temperature has fallen from L, fitted on exploration, compared with price.

Nothing here can show a strategy is profitable; a pass is a reason for a forward paper test.

### Amendment (before the data below was looked at): day-before prices

H4 found the morning-of market better than the National Blend of Models. H5 repeats H4 on the
**day before** (trades 40 → 25 h before close, ≈ 09:00 local on D − 1 to midnight, new pull
`raw/weather_d1/`), with the normal's bias and spread fitted on the run available at 12:00 local
on D − 1 for other days. Same split and judgement; frozen rule logged in CANDIDATES_V3.md before
any holdout run.
