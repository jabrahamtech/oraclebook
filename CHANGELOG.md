# Changelog

All notable changes to oraclebook are recorded here.

## [Unreleased]

### Fixed
- Paper fills were optimistic by construction (every order filled instantly, in full, at its limit
  price — a `CompleteSetMaker` backtest locked both legs every time). `PaperExecutor` now fills
  takers against visible depth and resting orders only on trade-through, with latency,
  FOK/FAK/GTD, cash/inventory reservation and venue maker/taker fees.
- Paper SELLs with no inventory minted cash; orders beyond cash or with NaN/negative size filled.
- Realised PnL was never tracked, so `DailyLossGate` never tripped; the Book now books trading,
  fee and settlement PnL and the App resets it per UTC day. `App.resolve` settles positions.
- Concurrent batches could race past gates; one executor/gate exception killed a consumer.
- Replay lookahead: the clock advanced to future events while a handler was still running, and
  the recorder assigned `seq` out of publish order. Replay is now deterministic across topics.
- Truncated recordings hung `run_replay`; re-running into one ledger/recording double-counted.
- Coalescing starved quiet tokens; resolutions were neither durable nor drop-proof.

### Added
- `OrderValidityGate`, `InventoryGate`; exposure gates count resting orders.
- `oraclebook.analysis`: markouts / adverse selection / realised spread, probabilistic and
  deflated Sharpe, bootstrap by market, Brier / log loss / calibration, fractional Kelly,
  purged walk-forward. `oraclebook replay --report` prints per-market markouts.

## [0.1.0] — unreleased

Initial public release.

- Event-driven core: `App` composition root, pub/sub `Bus`, protocol-based dependency
  injection (`Feed`, `Strategy`, `Gate`, `Executor`, `Venue`).
- Polymarket adapter (CLOB V2): live feed, paper and live executors, fee model.
- `Replay` backtesting on recorded order flow (`oraclebook record` / `oraclebook replay`) —
  reproducible for a single feed; fills simulated by the paper executor; cross-topic
  interleaving best-effort.
- Composable pre-trade risk gates (`FeedStalenessGate`, `DailyLossGate`, `BalanceGate`,
  `OrderSizeGate`, `PerMarketCapGate`, `TotalExposureGate`, `MaxPositionsGate`) plus a
  `CircuitBreaker`.
- `CompleteSetMaker` reference strategy, shipped candidly as a worked example of a strategy
  that doesn't survive real fill dynamics.
