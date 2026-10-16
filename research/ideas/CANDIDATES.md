# Frozen candidate rules (appended when chosen on exploration; holdout runs only at the end)

| id | idea | frozen rule | exploration result (net) |
|---|---|---|---|
| R1 | 5-min crypto favourites, outcomes rebuilt from Binance | kachoio, 240 s before scheduled close, buy any side with ask >= 0.90 (spread <= 5c), taker, fee 0.07·p·(1−p) | +1.58% [+0.19, +2.91], n=832, 36 days — weak; 1 positive cell of 12 |
| R2 | post-event harvest (World Cup match markets) | at kickoff + 125 min, still open, buy any side with ask in [0.98, 0.999), spread <= 5c, taker, fee 0.07·p·(1−p), hold to resolution | +0.95% [+0.88, +1.02], n=45, 29 events, 0 losers — too few trades to pass the 200-trade bar; tail risk unbounded by this sample |
| R3 | maker sells World Cup longshots (trade tape) | 1 day before end_date, rest an offer at the YES best ask when YES mid in [0.01, 0.10) (= buy NO at 1 − ask), spread <= 5c, cancel at end_date, filled only on trade-through, no fee, hold to resolution | +1.02% [−0.44, +2.26], 924 fills, 249 events (taker version −0.30%) |
| R4 | Kalshi BTC 15-min maker buys the favourite | 5 min before close, rest a bid on whichever side has mid in [0.90, 0.98) at its best bid, filled only if a later candle trades through, maker fee 0.0175·p·(1−p), hold to resolution | +0.64% [−0.56, +1.83], 1,169 fills, 41 days |

## Holdout (each run once, α = 0.05/4, 98.75% CI)

| id | holdout | passes? |
|---|---|---|
| R1 | −0.76% [−3.35, +1.97], 682 trades, 19 days | no (sign flipped) |
| R2 | +0.65% [+0.46, +0.86], 33 trades, 21 events | no (< 200 trades; tail risk unmeasured) |
| R3 | +1.14% [−0.81, +2.85], 757 fills, 169 events | no (CI spans 0); sign held — lead for a paper test |
| R4 | −0.35% [−2.57, +1.76], 781 fills, 27 days | no |
