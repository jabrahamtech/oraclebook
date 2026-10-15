# Round 5: Kalshi soccer against Betfair Exchange closing odds — results

Protocol: [PROTOCOL_V5.md](PROTOCOL_V5.md) (committed before any outcome was looked at). Data:
Kalshi 1X2 markets in 17 European leagues, every trade from about 3 h before kick-off, joined to
football-data.co.uk closing odds — **4,830 matches** (96% of Kalshi's events; Kalshi's result
agrees with the final score on every joined market). Exploration 2025/26 (3,760 matches),
holdout 2026-08-15 → 2026-10-14 (1,070 matches). **Nothing here shows a profitable strategy.**

## Verdict

1. **Kalshi soccer prices are as sharp as Betfair Exchange at kick-off.** Five minutes before
   kick-off, Kalshi's quote mid and de-vigged Betfair closing odds have the same Brier score
   (0.1965 vs 0.1964, 7,989 markets) and differ by 0.9¢ on average; the bookmaker average is
   no better (0.1967). On the holdout: 0.2002 vs 0.1997, 0.7¢ apart.
2. **Buying toward the sharp price does not pay.** At edge 0 the rule trades often and loses or
   breaks even (+6.0% [−5.8, +18.8] at τ = 5 min; −3.8% [−13.0, +5.7] at τ = 30 min; −10.3% and
   −5.3% against the bookmaker average). The one cell that cleared the exploration bar
   (τ = 5 min, Betfair, edge 2¢: +95% [+24, +180]) rested on 9 trades and $4 of cost — 1 of 12
   cells. Frozen and run once on the holdout it fired **2 times in 1,070 matches**:
   +215% [−100, +331] → fail.
3. Gaps between Kalshi and Betfair bigger than the fee plus 2¢ are rare (about 1 market in 900
   five minutes before kick-off), so even a real edge there would be too small to matter.

## Exploration grid (2025/26)

| τ before kick-off | Benchmark | Edge | Trades | Return [95% CI] |
|---|---|---|---|---|
| 5 min | Betfair | 0 | 258 | +5.97% [−5.83, +18.83] |
| 5 min | Betfair | 0.02 | 9 | +95.4% [+23.6, +180.2] → frozen |
| 5 min | Betfair | 0.05 | 3 | +252% [−100, +289] |
| 5 min | Average | 0 | 1,020 | −10.29% [−21.50, +1.17] |
| 5 min | Average | 0.02 | 79 | +10.6% [−27.8, +50.8] |
| 30 min | Betfair | 0 | 454 | −3.78% [−13.03, +5.65] |
| 30 min | Betfair | 0.02 | 33 | +7.3% [−31.1, +46.8] |
| 30 min | Average | 0 | 1,026 | −5.34% [−15.78, +5.15] |
| 30 min | Average | 0.02 | 89 | −6.7% [−35.9, +23.7] |

Holdout: τ = 5 min, Betfair, edge 0.02 → 2 trades, +215% [−100, +331] (α = 0.05) → **fail**.

## Notes

- Pinnacle closing odds stop part-way through 2025/26 (its public feed closed), so the sharp
  benchmark is Betfair Exchange closing.
- Older Kalshi soccer markets have no `occurrence_datetime`; their `expected_expiration_time`
  (kick-off + 3 h) is used instead. `kalshi_api.py` now falls back to it for occurrence windows.
- Quotes are last-trade prints (YES-taker ≈ ask, NO-taker ≈ 1 − bid) at most 15 min old; 76% of
  markets have a YES quote and 33% a NO quote five minutes before kick-off.

## Also this session: the 15-minute vs hourly crypto order-book recorder

1,171 order-book polls over 9 hourly windows ending 2026-10-15 ~03:00 UTC: 5 crossings between
the 15-minute and hourly BTC/ETH markets, one positive after fees (+0.7¢ per contract, 538
contracts, gone within 10 s). The two markets sat 3¢ apart on average with a 1¢ spread on the
15-minute contract. No tradeable arbitrage.

## Reproduce

```bash
cd research/data
SER=KXEPLGAME,KXEFLCHAMPIONSHIPGAME,...   # the 17 series in soccer.py LEAGUES
python -I kalshi_api.py raw/soccer_hist --series $SER --start 2025-07-01 --end 2026-08-15 \
    --occurrence-window 6,2.5 --historical
python -I kalshi_api.py raw/soccer_live --series $SER --start 2026-08-15 --end 2026-10-15 \
    --occurrence-window 6,2.5
# football-data.co.uk: mmz4281/{2526,2627}/{E0,E1,E2,SC0,D1,D2,I1,I2,SP1,SP2,F1,F2,N1,B1,P1,T1,G1}.csv
#   saved as raw/football_data/<season>_<league>.csv
cd ../ideas
python -I soccer.py build && python -I soccer.py explore
python -I soccer.py holdout --tau 5 --edge 0.02 --bench bfe
```
