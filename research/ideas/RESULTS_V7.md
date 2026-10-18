# Round 7: reverse the best current crypto wallets — not run yet

Protocol: [PROTOCOL_V7.md](PROTOCOL_V7.md). Status 2026-10-19: no data fetched, no wallet
selected, no rule frozen. Every source this round needs except Binance is on
`*.polymarket.com` (Gamma, data-api, lb-api), and none of those hosts resolves from the laptop
that ran round 6.

## Ready

- `research/data/polymarket_api.py --trades --both-sides` sends `takerOnly=false` and writes
  `trades_all.parquet`. The file is separate so that the taker-only tape (P3) and the both-sides tape
  never mix in the resumable cache.
- Binance 1-second klines, checked with one live call
  (`data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-2026-10-14.zip`): no
  header, the same 12 columns as the 1-minute files, open time in microseconds, 86,400 rows a
  day, about 2.4 MB zipped per coin-day.

## To do once the Polymarket hosts resolve

1. Make one live call each to Gamma, data-api (`takerOnly=false`) and lb-api before writing any
   parsing code. The open question is how a maker fill is told apart from a taker fill in the
   both-sides tape.
2. Fetch BTC and ETH Up/Down markets at 5m, 15m and 1h, closing in the 30 days before the run date:
   `polymarket_api.py raw updown --interval <iv> --start <run date − 30 d> --end <run date>
   --trades --both-sides`.
3. Implement and run the exploration half: PnL, eligibility and top 10, then fingerprints,
   archetypes and frozen rules. Then report one line per wallet.
