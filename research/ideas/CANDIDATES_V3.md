# Round 3 frozen rules (appended when chosen on exploration; holdout runs once at the end)

| id | idea | frozen rule | exploration (net) |
|---|---|---|---|
| H1 | take decided weather contracts | wx_decided.py taker view | no entries: after the METARs decide a contract no taker ever buys it below 99¢ (all decided-side buys are resting bids) → **nothing to freeze** |
| H3 | "peak already passed" nowcast | wx_nowcast.py | market Brier far better (hour ≥ 16: 0.014 vs 0.125; 3 cities) — traders see minute data, not hourly METARs → **dropped** |
| H4 | National Blend of Models vs morning prices | wx_nbm.py, normal(txn + b·xnd, s·xnd) fitted on other days | Brier market 0.156, NBM 0.193, 50/50 blend 0.164 (20 cities, 534k prints); taker rules −6% to −8% → **dropped** |
| H5 | NBM vs day-before prices (amendment) | wx_nbm.py --tape d1 | Brier market 0.162, NBM 0.191, blend 0.169 (20 cities, 470k prints, 09:00 → 24:00 local on D − 1); taker rules −4% to −10% → **dropped** |
| M1 | makers buying decided weather contracts (population) | wx_decided.py `--maker`, delta 1: every pm-tape print (20 cities, 12 → 0 h before close) where a taker sold the side that the confirmed METAR maximum (spike filter, 10-min lag) had decided with a 1 °F margin; maker pays the print + maker fee; dollar-weighted, CI resampling city-days; pass if CI > 0 at α = 0.05 (the only frozen rule) | 12 cities: +1.01% [+1.01, +1.01], 2.66M contracts, 392 city-days, no losses; all 20 cities: +1.01% [+1.01, +1.01], 3.07M contracts, 645 city-days, no losses, ≈ $37.5 per city-day to the whole queue (delta 0 would have lost on 0.06% of decided markets). Measures what the resting 99¢ bids earned; a newcomer's share depends on queue position (live sampler, kx_live_wx.py) |
| L1 | M1 replicated on daily lows (frozen after M1's holdout, judged on its own at α = 0.05) | wx_decided.py `--tape low --maker`, delta 1: the same rule on the 20 daily-low series (KXLOWT…, full-day tape); the running *minimum* from spot METAR readings only (a corrupt 6-hour minimum on 2026-09-19 NYC, 8 °F below every reading, made the 6-hour groups unsafe for lows) | all 20 cities: +1.04% [+1.01, +1.09], 568,830 contracts, 368 city-days, no losses, ≈ $7.2 per city-day; taker buys of decided lows below 99¢ exist but are tiny (37 trades, $501, +2.21%) |

## Holdout (run once, α = 0.05)

| id | holdout | passes? |
|---|---|---|
| M1 | +1.01% [+1.01, +1.01], 7,193 trades, $1.59M, 297 city-days, no losses | yes — a population measure: it says the resting 99¢ bids on decided contracts earned 1% with no losses, not that a new bid would be filled (see RESULTS_V3.md, queue) |
| L1 | +1.03% [+1.01, +1.07], 1,358 trades, $236,532, 187 city-days, no losses | yes — same caveat as M1 (population of resting 99¢ bids, not a newcomer's fills) |
