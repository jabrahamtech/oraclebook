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
- `CompleteSetMaker` reference strategy, shipped candidly as a worked example of a strategy
  that doesn't survive real fill dynamics.
