# Idea tests: results (round 1)

Round 2, on fresh Kalshi data: [RESULTS_V2.md](RESULTS_V2.md).

Protocol: [PROTOCOL.md](PROTOCOL.md) (written before testing). Frozen rules: [CANDIDATES.md](CANDIDATES.md).
Every dataset was split by scheduled close: earliest 60% exploration, latest 40% holdout. About
700 rule/parameter cells were looked at on exploration; 4 frozen rules went to the holdout, each
run once, judged at α = 0.05/4. All returns are per $1 staked, net of the stated costs.
**Nothing here shows a profitable strategy.** These are historical samples of weeks to months,
most of them one product type.

## Verdict

No idea passed the decision rule. One lead is worth a forward (paper) test:

**Maker selling longshots on World Cup markets (R3).** Resting an offer at the best ask on YES
contracts priced under 10¢ (i.e. buying NO at ~90–99¢), filled only when the trade tape prints
through the price, cancelled at kick-off, held to resolution, no maker fee:
holdout +1.14% [−0.81, +2.85] (98.75% CI, 757 fills, 169 events), exploration +1.02%. Post-hoc
(not part of the decision): positive in all 8 period × horizon cells (+0.6% to +2.6%) while the
taker version of the same trade sits near zero (−0.6% to +0.4%). The cells share markets, so they
are not independent confirmations. One tournament, non-commercial data licence, sizes not modelled.

## What was tried

| # | Idea | Data | Result |
|---|---|---|---|
| 1 | Fair value from the underlying (random walk on spot vs strike) | Kalshi BTC 15m + Coinbase; Polymarket crypto Up/Down + Binance | **Dead.** Market beats the model at every horizon (Brier at 1 min: market 0.110, model 0.164). Spot adds ~nothing beyond the market price out of sample (log-loss gains ≤ 0.004, often negative). |
| 5 | 5-min crypto favourites underpriced? | kachoio, outcomes rebuilt from Binance (99.3% agreement where the publisher gave one) | **Dead.** Restoring the 12% close calls the publisher left null removes the effect: calibration slope 1.18–1.28 → ~1.04; favourites turn negative after fees at 30–120 s. The one positive cell (240 s, R1) flipped sign on holdout: −0.76% [−3.35, +1.97]. |
| 2a | Complete-set arbitrage (Up ask + Down ask < $1) | kachoio per-second books | **Dead.** The books are mirrored (Up bid + Down ask = $1.000 on every tick); 554 of 26.8M ticks show a gap after fees, consistent with snapshot timing. |
| 2b | Event-sum arbitrage (home/draw/away asks sum < $1, or bids > $1) | World Cup, 413 three-market matches | **Real but not capturable slowly.** Gaps after fees in 118 (YES) and 283 (NO) events, median duration 1 minute. Trading one minute after seeing a gap at the seen prices: only 35–40% of sets fill completely; partial sets lose (−47% YES, −6% NO per $1). Needs sub-second execution, which minute bars cannot test. |
| 3a | Maker instead of taker, quotes only | kachoio, polyorderbooks | **Dead.** Fills only happen when the price moves through the order, i.e. against it: −7% to −44% per $1 in every band/horizon, worse than taking. |
| 3b | Maker, real trade tape | World Cup | **Lead (R3).** See verdict. Buying favourites as a maker shows nothing. |
| 3c | Maker on Kalshi BTC 15m (candle trade high/low) | Kalshi | **Dead.** Mostly negative; the one positive pocket (favourite bid at 5 min, R4) gave −0.35% on holdout. |
| 4 | Momentum / reversal (recent move beyond current price) | World Cup, Kalshi, kachoio | **Dead.** The move adds no out-of-sample information; every rule loses after costs, reversal badly. |
| 6 | Post-event harvest (buy the decided side after full time) | World Cup | **Tiny premium, unbounded tail.** Clock-based (R2): holdout +0.65% [+0.46, +0.86] but only 33 trades. With the live score feed, one minute after the final whistle every implied side already trades at 99–99.9¢: +0.21% per trade, ~15 min to resolution, 0 losers in 138 (51 matches). One disputed resolution erases ~500 such trades; depth unknown. |

## Side results worth keeping

- Binance 1-minute prices reproduce the 5-minute crypto publisher's outcomes 99.3% of the time
  (77.8% when the move is under 0.5 bp), which settles the open question in the FLB study: the
  apparent +1–2% favourite return in dataset D came from dropping close calls.
- Kalshi's published strike sits ~1 bp (median) above Coinbase's price at the window open.
- After a World Cup match ends, the market reprices within a minute.

## Reproduce

```bash
cd research/data && python -I fetch.py raw && python -I fetch.py raw worldcup --optional && python -I panels.py raw panels
cd ../ideas
python -I fairvalue.py kalshi explore        # and: kachoio | polyorderbooks; mode info
python -I crypto_favourites.py explore
python -I event_sums.py --latency 1
python -I maker.py kachoio explore           # and: polyorderbooks
python -I maker_tape.py explore
python -I kalshi_maker.py explore
python -I momentum.py worldcup explore       # and: kalshi | kachoio
python -I harvest.py explore && python -I harvest_feed.py
# frozen holdouts (alpha 0.0125):
python -I crypto_favourites.py holdout --horizon 240s --lo 0.90 --alpha 0.0125
python -I harvest.py holdout --at 125min --lo 0.98 --hi 0.999 --alpha 0.0125
python -I maker_tape.py holdout --horizon 1d --side no --lo 0.01 --hi 0.10 --alpha 0.0125
python -I kalshi_maker.py holdout --horizon 5m --side fav --lo 0.9 --hi 0.98 --fee 0.0175 --alpha 0.0125
```
