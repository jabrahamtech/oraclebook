# Changelog

All notable changes to oraclebook are recorded here.

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
- `DepthPaperExecutor`: paper fills that walk recorded depth (VWAP across levels, liquidity
  consumed), apply order latency, fill partially by time-in-force, and rest GTC/GTD orders
  with a conservative trade-through model. Use it with `run_replay(..., fill_model="depth",
  latency_ms=...)` or `oraclebook replay --fill-model depth --latency-ms N`. The Polymarket
  feed now applies `price_change` level deltas, so recordings keep full depth between
  snapshots.
- `CompleteSetMaker` reference strategy, shipped candidly as a worked example of a strategy
  that doesn't survive real fill dynamics.
