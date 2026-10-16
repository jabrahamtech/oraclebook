# Protocol v5: Kalshi soccer against sharp closing odds (written before the data was looked at)

Rounds 1–4 found that Kalshi prices public information about as well as the models tried. This
round asks a narrower question that needs no model: do Kalshi's soccer 1X2 markets (home win /
draw / away win), whose flow is mostly retail, drift from the prices of a sharp venue at kick-off,
and does buying toward the sharp price pay after Kalshi's fees? Sharp closing prices (Pinnacle,
Betfair Exchange) are the standard benchmark of soccer pricing efficiency.

## Data

- **Kalshi**: settled `KX<league>GAME` markets for 17 European leagues (EPL, Championship,
  League One, Scottish Premiership, Bundesliga 1–2, Serie A–B, La Liga 1–2, Ligue 1–2,
  Eredivisie, Belgian Pro League, Liga Portugal, Süper Lig, Greek Super League), with every
  trade from 6 h to 2.5 h before `occurrence_datetime` (which is kick-off + 3 h for soccer), via
  `research/data/kalshi_api.py` (`/historical/*` before Kalshi's 2026-08-15 cutoff).
- **Closing odds**: football-data.co.uk season files for the same leagues, 2025/26 and 2026/27
  (`research/data/raw/football_data/`). Pinnacle closing odds stop part-way through 2025/26, so
  the sharp price is **Betfair Exchange closing (BFEC)**, made fair by normalising the implied
  probabilities to sum to 1. The bookmaker average closing price (AvgC), normalised the same way,
  is the secondary benchmark.
- **Kick-off** from football-data's Date + Time in UK local time. Matches are joined on league,
  kick-off date and team names (fuzzy); matches that fail the join, or whose kick-off differs
  from `occurrence_datetime − 3 h` by more than 15 min, are dropped (outcome-blind).

## Quotes

At decision time τ minutes before kick-off (τ ∈ {5, 30}), the YES ask of an outcome is the price
of its last YES-taker trade in the 15 minutes up to τ, and the NO ask is 1 − the price of its last
NO-taker trade in that window. Older or missing prints mean no quote. Trades after kick-off are
never used.

## Rule S5(τ, e, benchmark)

For each match, outcome and side, buy one contract at the ask when the benchmark's fair
probability exceeds the ask plus Kalshi's taker fee (series rate × p × (1 − p)) by more than e.
P&L: settlement − ask − fee. Return = Σ P&L / Σ cost, with a percentile bootstrap that resamples
matches (one cluster per match).

## Split

Exploration: matches before **2026-08-15** (the 2025/26 season). Holdout: 2026-08-15 → 2026-10-14
(the start of 2026/27), touched once per frozen rule.

Exploration grid: τ ∈ {5, 30}, e ∈ {0, 0.02, 0.05}, benchmark ∈ {BFEC, AvgC}. Also reported:
Brier and log-loss of Kalshi's quote mid versus each benchmark. A rule is frozen only if its
exploration return is positive with a 95% interval above zero; the holdout is judged at
α = 0.05 / (number of frozen rules).

## Caveats fixed in advance

- The closing odds are recorded around kick-off, i.e. up to τ minutes after the Kalshi quote; at
  τ = 30 that is information a live trader would not yet have had. τ = 5 is the closer match.
- Trading the rule live needs a live sharp price (e.g. the Betfair Exchange API) and Kalshi's
  real order book; last-trade prices approximate the book.
- Nothing here implies a profitable strategy.
