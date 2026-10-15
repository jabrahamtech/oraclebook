# Round 2: fresh Kalshi data — results

Protocol: [PROTOCOL_V2.md](PROTOCOL_V2.md) (written, with three amendments, before any analysis).
Frozen rules: [CANDIDATES_V2.md](CANDIDATES_V2.md). Data: settled Kalshi markets closing
2026-08-15 → 2026-10-14 with their trade tapes (taker side included) from the public API,
scoped as the amendments describe: 20 weather cities (every market, 06:00–10:00 local trades),
15 sports series (100 random markets each, trades 12 → 4 h before the event), hourly BTC/ETH
above/below and 15-minute BTC/ETH up/down (400 random markets each, last hour / last 15 min),
S&P 500 and Nasdaq daily ranges (100 random markets each). Exploration = closes before
2026-09-25; holdout = after. Returns are dollar-weighted per $1, net of each series' Kalshi fees.
**Nothing here shows a profitable strategy.**

## Verdict

1. **Round 1's lead does not replicate.** C1 (pre-registered): makers long the favourite against
   sports longshot takers, holdout +1.81% [−0.48, +3.52] (exploration −6.75% [−23.1, +3.2]).
   Selling longshots earns +2–8% per series in quiet weeks and loses it all on upsets.
2. **Takers lose almost everywhere; makers as a population earn; a naive maker does not.**

   | market | takers (all trades) | makers as filled | queue-aware resting quotes |
   |---|---|---|---|
   | weather, exploration | −3.58% [−5.45, −1.10] | +2.05% [+0.53, +3.51] (price 10–90¢) | −0.82% [−2.87, +1.28] |
   | weather, holdout | −6.37% [−8.97, −4.07] | **+3.68% [+0.00, +8.39]** (98.3% CI, V1) | +0.03% [−2.72, +2.67] |
   | BTC 15-min, exploration | −2.56% [−3.01, −2.08] | +0.62% [+0.15, +1.07] | −2% to −5% at every timing |
   | ETH 15-min, exploration | – | – | −2% to −5.5% at every timing |
   | BTC/ETH hourly, exploration | −3.16% [−5.29, −1.01] | +0.83% [−1.11, +2.79] | – |

   V1 (weather makers as a population) formally passes the holdout rule, by a hair (lower bound
   +0.00005%). But it measures what *other* makers' filled orders earned. Our queue-aware check —
   quote at the prevailing bid/ask, fill only when the market trades through, hold to settlement —
   earns nothing on weather and loses 2–5% on 15-minute crypto: passive quotes get filled just
   before the price moves against them. The population's edge belongs to makers who reprice
   faster than the flow that hits them. Capturing it would need exactly that (fast quote
   management, queue priority), which these data cannot test.
3. **Everything else failed.**

## What was tried

| Idea | Data | Result |
|---|---|---|
| Weather: forecast vs market | 20 cities, Open-Meteo archived forecasts (issued before day D), out-of-sample per day | **Dead.** Brier: market 0.144, model 0.173; buy-the-edge rule −2.4% at 08:00 local. Traders have that morning's NWS forecasts; a day-old public model is behind. |
| Hourly crypto fair value (spot random walk, basis from settlements) | BTC/ETH above/below + Binance | **Dead.** Market Brier better at 5–50 min; rules ≈ 0 or negative. Third venue/horizon with the same result as round 1. |
| Order flow: follow informed takers | weather, sports, hourly crypto, 15-min crypto | Weather, sports, 15-min crypto: no out-of-sample information. Hourly crypto: imbalance *is* informative (log-loss gain +0.018), following it +2.87% on exploration (K1) → **holdout −1.13% [−6.76, +3.35]**. |
| Order flow: fade one-sided takers | sports | Exploration monotone in the threshold up to +5.86% (S1) → **holdout −1.09% [−13.97, +11.82]**. |
| Maker vs taker by price band | all | Taker losses largest in cheap bands; no band where takers win reliably. |
| S&P 500 / Nasdaq ranges | 100 markets each | Too thin to test (4.1k / 1.7k trades). |

Holdout rules run once each at α = 0.05/3: V1 (pass, borderline, not actionable as shown), S1
(fail), K1 (fail). C1 judged separately as pre-registered (fail).

## What would be worth doing next

- A **forward paper test of an active weather market maker** (requote on every trade, cancel on
  adverse moves), since weather is where takers lose most and the population of makers earns —
  using the oraclebook paper executor against live Kalshi books. Historical trades cannot tell us
  how much of the makers' edge a new, slower participant would keep.
- The authenticated Kalshi API exposes historical order books; with them, queue position could be
  modelled instead of bounded by the trade-through rule.

## Reproduce

```bash
cd research/data
python -I kalshi_api.py raw --series KXHIGHNY,... --start 2026-08-15 --end 2026-10-15 --close-window 19,15
python -I kalshi_api.py raw --series KXNFLGAME,... --occurrence-window 12,4 --sample 100 --start ... --end ...
python -I kalshi_api.py raw --series KXBTCD,KXETHD --close-window 1,0 --sample 400 --start ... --end ...
python -I kalshi_api.py raw --series KXBTC15M,KXETH15M --close-window 0.25,0 --sample 400 --start 2026-08-21 --end 2026-10-15
cd ../ideas
python -I weather.py fetch && python -I weather.py explore
python -I kx_makers.py c1 && python -I kx_makers.py map KXNFLGAME
python -I kx_flow.py sports explore      # weather | crypto | crypto15
python -I crypto_hourly.py explore
python -I kx_maker_sim.py KXBTC15M explore
python -I kx_flow.py sports holdout --k 0.8 --min-dollars 100 --fade --alpha 0.016667
python -I kx_flow.py crypto holdout --k 0.2 --min-dollars 100 --alpha 0.016667
```
(The full series lists and fetch windows are in PROTOCOL_V2.md amendments and each
`raw/kalshi_api/<series>/_source.json`.)

Later rounds: [RESULTS_V3.md](RESULTS_V3.md) (weather after the observations, forecasts, live
structural checks) and [RESULTS_MACRO.md](RESULTS_MACRO.md) (macro nowcasts vs Kalshi).
