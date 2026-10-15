# Round 3: weather after the observations, forecasts, and live structural checks — results

Protocol: [PROTOCOL_V3.md](PROTOCOL_V3.md) (written before analysis). Frozen rules:
[CANDIDATES_V3.md](CANDIDATES_V3.md). Data: Kalshi daily-high markets, 20 cities, settled
2026-08-15 → 2026-10-14, trades 12 → 0 h before close (new pull); METARs and National Blend of
Models guidance from the Iowa Environmental Mesonet; live Kalshi books sampled 2026-10-14/15.
**Nothing here shows a profitable strategy.**

## Verdict

1. **The weather market is ahead of every public source tested.** Hourly METARs (H1, H3), the
   National Blend of Models on the morning (H4) and the day before (H5): the market's Brier score
   beats each, and every taker rule loses. Traders evidently use minute-level station data and
   better forecasts.
2. **One consistent positive, and it belongs to whoever is first in the queue.** Resting 99¢ bids
   on contracts the observations have already decided earned about +1% per fill with no losses,
   on daily highs (M1, holdout +1.01%, 297 city-days) and on daily lows (L1, holdout +1.03%, 187
   city-days). Both pass their holdouts, but they measure the existing queue. In the evening that
   queue (~12.7k contracts, median) is ~25× what later trades into it, so a newcomer joining late
   is not filled. Whether one joining at the moment of decision would be is being measured
   (`kx_live_wx.py` + `wx_queue.py`, after the sampled markets settle).
3. **No structural arbitrage that pays.** Bracket sums and strike ladders across ~14.5k open
   events: none positive after fees. Hourly vs 15-minute crypto (identical settlement number):
   live order books showed a genuine crossing (+4¢ gross, +0.7¢ net after both taker fees, 538
   contracts, gone within 6 s); historical prints cross in 17% of calm hours and 70% of volatile
   ones — a latency race where fees take most of the edge.

## What was tried

| Idea | Data | Result |
|---|---|---|
| METARs as a settlement oracle | 1,220 city-days | Day's METAR max (T-group + 6-hour max, spike filter) = settled high on 98.3%, above it on 2 days (by 1 °F). One raw METAR spike (80 °F in heavy rain, NYC 2026-08-27) is not in the climate report; the market ignored it correctly. |
| H1: take contracts the METARs have decided | pm tape, exploration | Nothing to take: once decided, the decided side trades at 99¢ and every decided-side buy is a resting bid. Prices are already at 98.5–99¢ before the hourly METAR that decides them (traders read minute data). |
| H3: "peak already passed" nowcast from hourly METARs | 3 cities, exploration | Dead. Brier at hour ≥ 16 local: market 0.014, nowcast 0.125. |
| H4: National Blend of Models (latest run, with its own spread) vs morning prices | 20 cities, 534k prints | Dead. Brier market 0.156, NBM 0.193, 50/50 blend 0.164; taker rules −6% to −8%. |
| H5: the same on the day before (thinner market?) | 20 cities, 470k prints | Dead. Brier market 0.162, NBM 0.191, blend 0.169; taker rules −4% to −10%. The market is ahead of the public forecast a day out as well. |
| M1: makers buying decided contracts at 99¢ (daily highs) | pm tape, 20 cities | Frozen; holdout **+1.01% [+1.01, +1.01]**, 297 city-days, no losses (exploration the same, 645 city-days). Population only: ≈ $37.5 per city-day to the whole 99¢ queue. |
| L1: the same on daily lows (replication) | full-day tape, 20 cities | Frozen after M1; holdout **+1.03% [+1.01, +1.07]**, 187 city-days, no losses; ≈ $7 per city-day. The 6-hour minimum groups had to be dropped (one corrupt group, 8 °F below every reading of the day, would have decided a contract wrongly). |
| Queue for a newcomer at 99¢ | live books, 2026-10-14 evening | Median ~12.7k contracts already queued at 99¢ on decided markets vs median ~470 sold into 99¢ bids per market after 8 pm historically: a late joiner is not filled. Decision-time queue build-up: pending (wx_queue.py on the 24-hour sample). |
| Live bracket sums (sell every YES above $1 / buy every YES below $1) | 14.5k open events per pass, 29 passes | `sell_all`: crossed sets exist (≈ 35 per pass, 1,021 in all) but none positive after fees (best −0.15¢). `buy_all` hits are all non-exhaustive candidate lists (primaries, "who will…" sets). |
| Live strike ladders | same (29 passes, 2026-10-14 23:58 → 10-15 02:05 UTC) | No tradeable violation: 7 positive-after-fee hits in 29 passes, all with ≤ 0.01 contracts displayed (dust; NBA totals, USD/SEK). Same-strike markets that differ by date or city are excluded. |
| Hourly vs 15-minute crypto (same settlement number) | history: 182 sampled hours, prints | Prints cross the riskless bound within 1 s in ~45% of hours (BTC 1¢, ETH 3¢ mean net), concentrated where the two contracts are near-identical and in the last minutes — consistent with one book repricing faster than the other, i.e. a latency race, not resting arbitrage. |
| — live | order books every 4 s, both legs, 15-min book read before and after | _pending_. (A first recorder used the market listing's top of book; it is cached and lags the order book by 10 s+, which manufactured a 4.7¢ "crossing". The bracket/ladder scanner used the same listing, so it sees books up to ~10 s+ old: it can miss brief crossings and show stale ones; none paid after fees.) |

## Reproduce

```bash
cd research/data
H=KXHIGHNY,KXHIGHCHI,KXHIGHMIA,KXHIGHLAX,KXHIGHAUS,KXHIGHDEN,KXHIGHPHIL,KXHIGHTSFO,KXHIGHTATL,KXHIGHTHOU,KXHIGHTDC,KXHIGHTSEA,KXHIGHTLV,KXHIGHTBOS,KXHIGHTPHX,KXHIGHTDAL,KXHIGHTMIN,KXHIGHTNOLA,KXHIGHTOKC,KXHIGHTSATX
python -I kalshi_api.py raw/weather_pm --series $H --start 2026-08-15 --end 2026-10-15 --close-window 12,0
python -I kalshi_api.py raw/weather_d1 --series $H --start 2026-08-15 --end 2026-10-15 --close-window 40,25
python -I kalshi_api.py raw/weather_low --series KXLOWTNYC,KXLOWTCHI,... --start 2026-08-15 --end 2026-10-15 --close-window 24,0
python -I iem_asos.py raw --stations NYC,MDW,MIA,LAX,AUS,DEN,PHL,SFO,ATL,HOU,DCA,SEA,LAS,BOS,PHX,DFW,MSP,MSY,OKC,SAT --start 2026-08-13 --end 2026-10-16
python -I iem_nbm.py raw --stations KNYC,KMDW,... --start 2026-08-12 --end 2026-10-15
cd ../ideas
python -I wx_decided.py explore                      # H1 + M1 (pm tape); --tape low for L1
python -I wx_decided.py holdout --delta 1 --maker    # M1
python -I wx_decided.py holdout --tape low --delta 1 --maker   # L1
python -I wx_nowcast.py explore --series KXHIGHNY,KXHIGHCHI,KXHIGHMIA   # H3
python -I wx_nbm.py explore                          # H4; --tape d1 for H5
python -I kx_xmkt_hist.py fetch && python -I kx_xmkt_hist.py analyse --window 1
# live (run for hours, then report)
python -I kx_live_arb.py ../data/raw/live_arb/scan.csv --minutes 120
python -I kx_live_xmkt.py ../data/raw/live_xmkt/ob.csv --minutes 360 --every 4
python -I kx_xmkt_report.py ../data/raw/live_xmkt/ob.csv
python -I kx_live_wx.py ../data/raw/live_wx/books.csv --minutes 1440
python -I wx_queue.py fetch ../data/raw/live_wx/books.csv && python -I wx_queue.py analyse ../data/raw/live_wx/books.csv
```
