# Round 2 frozen rules (appended when chosen on exploration; holdout runs once at the end)

| id | idea | frozen rule | exploration (net) |
|---|---|---|---|
| C1 | (pre-registered) makers vs sports longshot takers | PROTOCOL_V2.md C1, trades 12→4 h before occurrence | run at the end with the holdout |
| V1 | weather makers earn the spread | Kalshi daily-high markets, every city fetched (20 series listed in weather.py), trades 06:00–10:00 local on day D: the maker side of every trade whose maker price is in [0.10, 0.90]; dollar-weighted return net of the series' maker fee (0 for weather), CI resampling city-days | 8 cities: +2.67% [+0.77, +4.39]; all 20 cities: +2.05% [+0.53, +3.51], 388k trades, 819 city-days. Population of filled resting orders, not a queue-aware simulation |
| V1q | weather maker, queue-aware | at 07:00 local on day D quote YES bid / ask (last taker-sell / taker-buy prints of the previous hour) on markets with mid in [0.10, 0.90]; fill only if a later trade before 10:00 local prints through; hold to settlement | 8 cities: +0.98% [−1.80, +3.76]; all 20 cities: −0.82% [−2.87, +1.28] → **dropped** (not positive on full exploration; not taken to holdout) |
| S1 | fade one-sided taker flow in sports | sports series of C1; at occurrence − 5 h, if ≥ $100 of taker buys in the previous 7 h and \|imbalance\| > 0.8, buy the side takers were selling at its most recent taker-buy print (taker, fee 0.07·m·p·(1−p)); hold to settlement; CI resampling events | +5.86% [−1.06, +12.69], 565 trades, 502 events (monotone in the threshold: 0.0 / +0.9 / +2.3 / +5.9%) |
| K1 | follow taker flow in hourly crypto | KXBTCD + KXETHD; at close − 15 min, if ≥ $100 of taker buys in the previous 30 min and \|imbalance\| > 0.2, buy the side takers were buying at its most recent taker-buy print (taker fee); hold to settlement; CI resampling events | +2.87% [−0.31, +5.86], 226 trades, 213 events; imbalance adds out-of-sample information (log-loss gain +0.018) |

## Holdout (each run once; C1 at α = 0.05 as pre-registered; others at α = 0.05/3, 98.33% CI)

| id | holdout | passes? |
|---|---|---|
| C1 | +1.81% [−0.48, +3.52], 25,655 trades, 41 events | no |
| V1 | +3.68% [+0.00005, +8.39], 255,875 trades, 400 city-days | yes, borderline — a population measure; the queue-aware version earns +0.03% |
| S1 | −1.09% [−13.97, +11.82], 234 trades, 209 events | no (sign flipped) |
| K1 | −1.13% [−6.76, +3.35], 97 trades, 91 events | no (and < 200 trades) |
