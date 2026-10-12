# oraclebook

**oraclebook is an open-source Python framework for building, backtesting, and running event-driven trading strategies on prediction markets like Polymarket and Kalshi.**

[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **Status: pre-alpha.** oraclebook is being assembled from a production Polymarket trading bot. The API shown here is the design target and is **not yet published to PyPI**. Star/watch the repo to follow progress.

Write a trading strategy once, validate it on **real recorded order flow** with replay that is
deterministic (conservative trade-through paper fills; exact cross-topic ordering), then run it
in **paper** or **live**. oraclebook is extracted from a production
Polymarket trading bot and built on a clean, event-driven architecture (domain-driven design
with protocol-based dependency injection).

```bash
pip install oraclebook   # coming soon — not yet published to PyPI
```

## Motivation

I built a production Polymarket trading bot, ran it live, and measured, not assumed, that the
"obvious" edges (complete-set arbitrage, naive market-making) don't survive real fill dynamics:
you get adversely selected on exactly the leg you don't want. The infrastructure I built to *find
that out cheaply*, replay on recorded order flow (reproducible for a single feed; paper fills),
protocol-based DI, composable risk gates, turned out to be the durable, reusable part. oraclebook
is that infrastructure, extracted and open-sourced, with the losing strategy left in as a worked
example of why you should replay-test before you fund anything.

The complete-set strategy concept is documented in [polybot](https://github.com/ent0n29/polybot)
(MIT); oraclebook's implementation is an independent re-implementation.

## What is oraclebook?

oraclebook is a **Python framework for prediction-market trading**: automated, algorithmic
trading on venues such as **Polymarket** (with **Kalshi**, Limitless, and other venues added
through a small adapter interface). Instead of wiring together WebSocket feeds, an order
executor, risk checks, and a backtester from scratch, you get them as composable building blocks
and write only your strategy.

The whole framework is **five concepts**:

| Concept | What it does |
|---|---|
| **Feed** | Streams live order books and prices from a venue onto the event bus. |
| **Strategy** | Your trading logic, in one class — consumes events, emits orders. |
| **Gate** | A composable pre-trade risk check (notional caps, staleness, inventory…). |
| **Executor** | Places orders in **paper** (simulated) or **live** mode. |
| **Replay** | **Backtests on real recorded order books** (paper fills, reproducible per feed) — not a toy simulator. |

## Why oraclebook?

- **Backtesting on real recorded order flow.** Record the live order *book* once, then replay it
  through any strategy on a virtual clock — reproducible for a single feed. Your strategy sees the
  exact book updates that happened; fills are simulated by the paper executor (replaying real
  maker fills via the venue's user-WS is a planned follow-on).
- Most open-source trading bots are tangled monoliths. oraclebook keeps a **clean event-driven
  architecture** instead: a pub/sub message bus, protocol-based dependency injection, and bounded
  contexts, so the codebase stays modular and testable as it grows.
- **Venue-agnostic.** Polymarket is the reference adapter; add Kalshi or any prediction market by
  implementing four methods on a `Venue`.
- It ships with a real Polymarket market-making strategy *and* a candid, data-driven analysis of
  **why naive prediction-market market-making tends to lose money** — that's **honest by design**,
  because the point of a backtesting framework is to find out cheaply whether a strategy holds up.

## Builder attribution

The live Polymarket executor attaches the maintainer's Polymarket Builder Code to orders by
default. This earns the maintainer a share of Polymarket's weekly builder-rewards pool based on
trading volume, and it costs you nothing: it is paid by Polymarket from a separate rewards pool,
never deducted from your fills, and this framework's own builder fee is hard-set to 0 bps (both
maker and taker), so no fee is ever added to your orders. To opt out entirely, set
`POLYMARKET_DISABLE_BUILDER_ATTRIBUTION=true` (or set `POLYMARKET_BUILDER_CODE` to your own code).
The active code is logged at startup so it is always visible. See
[Polymarket's builder docs](https://docs.polymarket.com/builders/overview).

## Quickstart

```python
from oraclebook import App, BaseStrategy, OrderRequest, Side, OrderType
from oraclebook.adapters.polymarket import PolymarketVenue

class EdgeMaker(BaseStrategy):
    name = "edge_maker"
    async def on_book(self, book) -> list[OrderRequest]:
        edge = 1.0 - (book.yes_bid + book.no_bid)
        if edge < 0.02:
            return []
        return [ OrderRequest(token_id=book.yes_token, market_id=book.market_id,
                              side=Side.BUY, price=book.yes_bid, size=10,
                              order_type=OrderType.LIMIT, strategy=self.name) ]

App(venue=PolymarketVenue(assets=["BTC"], intervals=[5]),
    strategy=EdgeMaker(), executor="paper").run()
```

Backtest it on recorded order flow:

```bash
oraclebook record --venue polymarket --assets BTC --intervals 5 --out recordings/
oraclebook replay --strategy mymodule:EdgeMaker --from recordings/ --report
```

See **[GETTING_STARTED.md](GETTING_STARTED.md)** for the full walkthrough and
**[NAMING.md](NAMING.md)** for the architecture diagram.

## How oraclebook compares

- **vs a Polymarket API client** (e.g. `py-clob-client-v2`): oraclebook is not just an exchange
  wrapper — it includes feeds, strategies, composable risk gates, order execution, an append-only
  event log, and Replay backtesting on recorded order flow.
- **vs generic backtesters** (Backtrader, vectorbt): oraclebook replays *recorded prediction-market
  order books* (the real book updates that happened, with paper fills), not generic OHLCV candles.
- **vs single-purpose Polymarket bots:** oraclebook is venue-adapter based and built for research,
  paper trading, and live execution across prediction markets — not one hard-coded strategy.

## Supported venues

- **Polymarket**: the reference adapter (CLOB order book, on-chain settlement).
- **Bring your own**: implement a `Venue` (Feed + Executor + settlement + fees) to add
  **Kalshi**, **Limitless**, **Azuro**, or any prediction market.

## Known limitations

- Resting maker fills via the user WebSocket are not yet wired in the live executor (documented
  in its docstring).
- The feed uses a single WS connection (the production bot this was extracted from ran redundant
  connections).
- Live execution is experimental pre-alpha.

## FAQ

**What is oraclebook?**
oraclebook is an open-source Python framework for building, backtesting, and running
event-driven trading strategies on prediction markets such as Polymarket and Kalshi.

**How do I backtest a Polymarket trading strategy in Python?**
Record live order-book flow with `oraclebook record`, then run `oraclebook replay` to re-run
your strategy over it on a virtual clock and get a PnL/markout report — no
live risk, and reproducible for a single feed.

**Which prediction markets does oraclebook support?**
Polymarket out of the box (the reference adapter). Other venues — Kalshi, Limitless, and more —
are added by implementing a small `Venue` interface (four methods).

**Is oraclebook free and open source?**
Yes. oraclebook is MIT-licensed and free to use.

**How is oraclebook different from writing my own trading bot?**
It gives you the hard, generic parts — WebSocket feeds, an order-lifecycle executor, composable
risk gates, an append-only event log, and a replay backtester (reproducible per feed) — already built and
tested, so you write only your strategy.

**Can oraclebook trade live, or only backtest?**
Both. Swap the executor from `"paper"` to a live venue executor; always validate in paper and
Replay first.

**If your strategy loses money, why publish this?**
Because the framework, feeds, risk gates, replay backtesting, is the reusable part, and knowing a
strategy loses money *before* funding it is the entire value of replay-testing. Sharing the infra
costs me nothing; sharing a strategy that "worked" would just mean it hasn't been arbitraged away
yet.

## Build with AI coding agents

oraclebook is designed to be extended with **AI coding agents** (Claude Code, Cursor, and
similar). The repo ships an **[AGENTS.md](AGENTS.md)** that teaches an agent the architecture,
naming conventions, and extension recipes, so you can just ask:

- *"Add a mean-reversion Strategy that fades moves greater than 3% in the last minute."*
- *"Add a Kalshi Venue adapter."*
- *"Add a max-drawdown Gate that halts trading at −5% daily PnL."*
- *"Backtest my strategy on last week's recordings with Replay and report PnL and markouts."*

The agent reads `AGENTS.md`, follows the conventions, and writes the Strategy / Gate / Venue with you.

## Documentation

- **[Getting Started](GETTING_STARTED.md)** — install + the five-concept quickstart.
- **[AGENTS.md](AGENTS.md)** — building with Claude Code & AI coding agents.
- **[Naming & Architecture](NAMING.md)** — vocabulary and the system diagram.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — dev setup and PR guidelines.
- **[SECURITY.md](SECURITY.md)** — reporting a vulnerability.
- **[CHANGELOG.md](CHANGELOG.md)** — release history.

## Disclaimer

oraclebook is research and infrastructure software, not financial advice or a promise of profit;
live prediction-market trading can lose money.

## License

[MIT](LICENSE) — free for commercial and personal use.
