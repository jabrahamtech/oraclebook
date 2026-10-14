# Protocol v2: fresh Kalshi data (written before any of it was analysed)

Round 1 ([PROTOCOL.md](PROTOCOL.md), [RESULTS.md](RESULTS.md)) used up the holdouts of the six
original datasets. Round 2 uses data none of round 1 touched: settled Kalshi markets closing
**2026-08-15 → 2026-10-14**, pulled from the public API with their full trade tape
(`research/data/kalshi_api.py`): weather (daily highs, ~20 cities), sports (game winners across
leagues, MLB first-inning runs, UFC, tennis), crypto (15-minute up/down, hourly above/below and
ranges), equity indices (S&P 500 / Nasdaq daily ranges and hourly above/below).

## Split

By market close time: **exploration** = closes before 2026-09-25 00:00 UTC; **holdout** = closes
from 2026-09-25 to 2026-10-14. Holdout data is not looked at except to run frozen rules once.

## One pre-registered confirmatory test (C1): does round 1's only lead replicate?

Round 1's lead (R3) was "makers who sell longshots on sports markets before the event earn a
small positive return". On Kalshi sports game markets, using the trade tape: every trade before
the event's start in which the taker bought the side priced <= 10¢ makes the maker long the
other side at >= 90¢. Maker return per $1 = (payout − maker price − maker fee) / (maker price +
maker fee), maker fee from the series' fee type (0.0175·m·p·(1−p) where the series charges makers,
else 0; m = the series fee multiplier; no per-order rounding, i.e. assuming size). Contract-
weighted mean; 95% CI resampling events. **C1 passes if the holdout CI is above zero.** Reported
for exploration too. This measures what the population of resting orders earned, which a new
maker would only match with comparable queue position; that caveat stays attached.

## Everything else is exploratory

New ideas are developed on exploration; each idea gets one frozen rule (logged in
CANDIDATES_V2.md before its holdout run); all frozen rules are then run once on the holdout and
judged at α = 0.05 / (number of frozen rules), with ≥ 200 trades over ≥ 20 clusters and the same
sign as exploration. Costs always on: takers pay the ask (the trade price of a taker buy) and
0.07·m·p·(1−p); makers fill only on trade-through (or, for population estimates, as recorded).

Nothing here can show a strategy is profitable; a pass is a reason for a forward paper test.

### Amendment (still before any analysis): "before the event" for C1

Kalshi sports markets carry no start time; `occurrence_datetime` is the expected *end* (e.g. NFL
kickoff + 3 h). C1 therefore counts only trades created more than **4 hours before
`occurrence_datetime`** (or before `close_time` when it is missing), which excludes in-play
trading for every sport in the list except unusually long tennis matches.

### Amendment 2 (before any analysis): fetch windows and sampling

The public API allows ~20 requests/s and busy markets carry 10^4–10^5 trades, so the fetch is
scoped (all recorded in each series' `_source.json`):
- weather: every settled market, trades 19 → 15 h before close (≈ 06:00–10:00 local on day D);
- sports (C1): an outcome-blind random sample of up to 150 markets per series (seed = CRC32 of
  the series ticker), trades 28 → 4 h before `occurrence_datetime`. C1 is therefore evaluated on
  the last day before each event, not the whole pre-event life of the market;
- crypto hourly above/below: random 600 markets per series, last hour before close; crypto
  15-minute: random 600 markets per series, last 15 minutes; S&P / Nasdaq daily range: random
  150 markets per series, full tape.

### Amendment 3 (before any analysis): rate limit

The public API sustains only ~6 requests/s, so the fetch runs as one sequential job and the
heavier pulls are narrowed: sports — 100 sampled markets per series, trades **12 → 4 h** before
`occurrence_datetime` (C1 is evaluated on that slice); crypto hourly above/below and 15-minute —
400 sampled markets per series; S&P / Nasdaq ranges — 100 sampled markets per series. Weather is
unchanged (every market, 06:00–10:00 local).
