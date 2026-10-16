# AGENTS.md — building with oraclebook using Claude Code & AI coding agents

This file orients AI coding agents (Claude Code, Cursor, and similar) working in the **oraclebook**
repo, and shows humans how to drive an agent to extend the framework. oraclebook is an
event-driven Python framework for prediction-market trading (Polymarket, Kalshi, and other venues
via adapters). If you are an agent: read this, then [NAMING.md](NAMING.md) and
[GETTING_STARTED.md](GETTING_STARTED.md), before writing code.

## Mental model (read first)

The framework is **five concepts** — a `Feed` streams events onto the `Bus`; a `Strategy` turns
them into orders; `Gate`s vet the orders; an `Executor` places them (paper or live); `Fill`s
update the `Book` and `Ledger`; and `Replay` re-runs the recorded **feed** for backtesting
(deterministic; conservative trade-through paper fills; settle with `App.resolve`). A `Venue` bundles a venue's Feed + Executor + settlement + fees. See the diagram in
[NAMING.md](NAMING.md).

## House rules for agents

- **Match the naming conventions** in [NAMING.md](NAMING.md): `<Source>Feed`, `<Venue>Executor`,
  `<Check>Gate`, `<Venue>Venue`, descriptive `Strategy` names, past-tense event facts.
- **Depend on protocols, not concrete classes** (`oraclebook/core/protocols.py`). Strategies never
  call the executor or mutate positions directly — they emit `OrderRequest`s onto the bus.
- **Keep code clean** — no keyword-stuffed comments, no dead code, no docstrings on unchanged code.
  Python 3.11+, `ruff` (line length 100), `structlog` (never `print`).
- **Validate before claiming done:** run the relevant tests, then a **paper** run and a **Replay**
  backtest. Never imply a strategy is profitable — this is research/infra software.
- **Never embed secrets.** Adapters read credentials from environment variables only.

## Extension recipes

| You want to… | Do this |
|---|---|
| Add trading logic | Subclass `BaseStrategy` (`oraclebook/strategies/base.py`) in `oraclebook/strategies/`, override the handlers you need (`on_book` / `on_price` / `on_boundary`), return `OrderRequest`s. (`Strategy` in `core/protocols.py` is the structural Protocol those handlers satisfy — depend on it, don't subclass it.) |
| Add a risk check | Implement the `Gate` protocol (`name` + `check(orders, ctx) -> RiskCheck` — no base class to subclass), register it on the `App`. Built-ins live in `oraclebook/risk/gates.py`. |
| Add a data source | Implement the `Feed` protocol (`start` / `stop`), publish events onto the bus. |
| Add a whole venue | Implement `Venue` (`feed(bus)`, `executor`, `current_markets`, `fee`) in `oraclebook/adapters/<venue>/`; copy `adapters/polymarket/` as the reference. |
| Backtest | Capture with `oraclebook.replay.Recorder`, then re-run via `oraclebook.replay.run_replay(...)` (or wire a `ReplayFeed` + `ReplayClock` into an `App` yourself) — reproducible for a single feed; fills are simulated by the paper executor. |

## Example prompts (copy-paste to your coding agent)

- *"Read AGENTS.md and NAMING.md. Add a `MeanReverter` Strategy that fades any move greater than
  3% in the last 60 seconds, sized at $10, and add a unit test."*
- *"Add a `MaxDrawdownGate` that halts new orders once daily PnL is below −5%, following the Gate
  convention, with a test."*
- *"Scaffold a `KalshiVenue` adapter in `oraclebook/adapters/kalshi/` implementing the Venue
  protocol — feed, executor, current_markets, fee — reading credentials from env vars."*
- *"Backtest the `edge_maker` strategy on `recordings/` with Replay and summarize PnL and 60s
  markouts; do not characterize it as profitable, just report the numbers."*

## Where things live

```
oraclebook/core/        # Bus, App, models, events, protocols, clock, ledger (venue-agnostic)
oraclebook/risk/        # Gates + circuit breaker
oraclebook/replay/      # recorder + replay feed (backtesting on recorded flow)
oraclebook/strategies/  # Strategy base + examples (CompleteSetMaker)
oraclebook/adapters/    # Venue adapters (polymarket = reference)
```
