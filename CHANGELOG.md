# Changelog

All notable changes to oraclebook are recorded here.

## [0.1.0] — unreleased

Initial public release.

### Fixed (pre-release review)

- App: an executor/gate/ledger error no longer kills that topic's consumer; `stop()` waits for
  in-flight order batches so venue-accepted fills reach the Book and Ledger; resolutions are
  written to the Ledger; a ledger write error no longer drops the rest of a fill batch.
- Bus: coalescing delivers the oldest pending key first, so one busy token can't starve others.
- Paper executors: no SELL beyond the shares held; `DepthPaperExecutor` also caps BUYs by cash,
  reserves cash for working BUYs, expires GTD orders that arrive late, and fills orders that
  arrived blind as takers at the next book's prices.
- Gates: SELL-only batches pass `MaxPositionsGate`/`TotalExposureGate`; at-limit orders are no
  longer rejected by float error.
- Replay: a bad recording raises instead of hanging; the drain waits for every handler; the
  Recorder writes its unconsumed tail on stop and continues `seq` when appending; depth levels
  replay as tuples; events replay in published order across topics; a reused `ledger_path`
  holds only the latest run.
- CompleteSetMaker: a vanished bid isn't quoted; an edge exactly at the floor quotes.
- Polymarket: the feed re-discovers rolling markets and subscribes new tokens, runs one pinger
  per socket, and skips a malformed level instead of dropping the connection; the live
  executor refuses FOK/FAK/MARKET orders rather than posting them as resting limits, and reads
  fill size/price from the side-correct maker/taker amounts.
- `Strategy` protocol declares the handlers the App calls.

- Event-driven core: `App` composition root, pub/sub `Bus`, protocol-based dependency
  injection (`Feed`, `Strategy`, `Gate`, `Executor`, `Venue`).
- Polymarket adapter (CLOB V2): live feed, paper and live executors, fee model.
- `Replay` backtesting on recorded order flow (`oraclebook record` / `oraclebook replay`) —
  reproducible for a single feed; fills simulated by the paper executor; cross-topic
  interleaving best-effort.
- Composable pre-trade risk gates (`FeedStalenessGate`, `DailyLossGate`, `BalanceGate`,
  `OrderSizeGate`, `PerMarketCapGate`, `TotalExposureGate`, `MaxPositionsGate`) plus a
  `CircuitBreaker`.
- `DepthPaperExecutor`: paper fills that walk recorded depth (VWAP across levels, liquidity
  consumed), apply order latency, fill partially by time-in-force, and rest GTC/GTD orders
  with a conservative trade-through model. Use it with `run_replay(..., fill_model="depth",
  latency_ms=...)` or `oraclebook replay --fill-model depth --latency-ms N`. The Polymarket
  feed now applies `price_change` level deltas, so recordings keep full depth between
  snapshots.
- `CompleteSetMaker` reference strategy, shipped candidly as a worked example of a strategy
  that doesn't survive real fill dynamics.
