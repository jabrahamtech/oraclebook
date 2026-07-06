# Getting Started with oraclebook

**oraclebook** is an event-driven framework for **prediction-market** trading: write a
strategy once, run it in paper or live, and backtest it on *real recorded order flow*
(reproducible for a single feed; paper fills; cross-topic interleaving best-effort). Polymarket is the reference [Venue](NAMING.md); the seams are open so
you can add others.

> **Status: pre-alpha.** The API below is the design target as the framework is
> extracted from a live production Polymarket bot. Naming is stable across this
> pre-alpha (see [NAMING.md](NAMING.md)).

## Install

```bash
pip install oraclebook   # coming soon — not yet published to PyPI
```

## The whole framework is five verbs

> **write a Strategy · add a Gate · add a Feed · add a Venue · run Replay.**
> Everything else (App, Bus, Book, Ledger, Clock) is wiring oraclebook handles for you.
> See [NAMING.md](NAMING.md) for the vocabulary and the diagram.

---

## 1. Write a `Strategy` — your edge in one class

A strategy consumes events and returns orders. It never touches the executor or positions
directly — it just emits `OrderRequest`s; the framework routes them through your gates.

```python
from oraclebook import BaseStrategy, OrderRequest, Side, OrderType
from oraclebook.core.events import BookUpdate

class EdgeMaker(BaseStrategy):
    name = "edge_maker"

    def __init__(self, min_edge: float = 0.02, size: float = 10.0):
        self.min_edge = min_edge
        self.size = size

    async def on_book(self, book: BookUpdate) -> list[OrderRequest]:
        # quote both sides only when the complete-set edge clears the floor
        edge = 1.0 - (book.yes_bid + book.no_bid)
        if edge < self.min_edge:
            return []
        return [
            OrderRequest(token_id=book.yes_token, market_id=book.market_id,
                         side=Side.BUY, price=book.yes_bid, size=self.size,
                         order_type=OrderType.LIMIT, strategy=self.name),
            OrderRequest(token_id=book.no_token, market_id=book.market_id,
                         side=Side.BUY, price=book.no_bid, size=self.size,
                         order_type=OrderType.LIMIT, strategy=self.name),
        ]
```

## 2. Run it — in paper first

The `App` is the composition root: hand it a Venue, a Strategy, and an Executor, then `run()`.
`executor="paper"` simulates fills against the live book — **no real money, no keys.**

```python
from oraclebook import App
from oraclebook.adapters.polymarket import PolymarketVenue

app = App(
    venue=PolymarketVenue(assets=["BTC"], intervals=[5]),  # BTC 5-minute up/down
    strategy=EdgeMaker(min_edge=0.02),
    executor="paper",
)
app.run()   # subscribes the strategy to the bus, starts feeds, runs the loop
```

Going live is a one-word change (`executor="polymarket"`) plus credentials in `.env`.
**Always validate in paper + Replay first.**

## 3. Backtest with `Replay` — the headline feature

Record the real order-book flow once, then replay it through *any* strategy. Because the `Clock`
advances off the recorded events (not the wall), a single-feed replay is reproducible — your
strategy sees the exact book updates that happened. Fills are simulated by the paper executor
(replaying real maker fills via a venue's user-WS is a planned follow-on), and across multiple
topics the interleaving is best-effort — so keep a backtest to one feed for strict reproducibility.

```bash
# capture live order flow to JSONL recordings
oraclebook record --venue polymarket --assets BTC --intervals 5 --out recordings/

# replay it through your strategy (dotted module:ClassName path) into a fresh
# Ledger, then report fills/PnL
oraclebook replay --strategy mymodule:EdgeMaker --from recordings/ --report
```

## 4. Add a `Gate` — a pre-trade risk check

Gates are ordered cheapest-first and short-circuit. Register them on the `App`; every order
batch passes through before the executor sees it.

```python
from oraclebook import Gate, RiskCheck, RiskContext, OrderRequest

class MaxNotionalGate(Gate):
    name = "max_notional"
    def __init__(self, cap_usd: float):
        self.cap_usd = cap_usd

    def check(self, orders: list[OrderRequest], ctx: RiskContext) -> RiskCheck:
        notional = sum(o.price * o.size for o in orders)
        if notional > self.cap_usd:
            return RiskCheck(False, f"notional ${notional:.0f} > cap ${self.cap_usd:.0f}", self.name)
        return RiskCheck(True)

# built-in gates: FeedStalenessGate, DailyLossGate, BalanceGate, OrderSizeGate,
# PerMarketCapGate, TotalExposureGate, MaxPositionsGate (+ CircuitBreaker)
app = App(..., gates=[MaxNotionalGate(cap_usd=50), FeedStalenessGate(2000)])
```

## 5. Add a `Venue` — a whole new prediction market

A `Venue` bundles everything market-specific. Implement the four seams and the kernel
orchestrates the rest unchanged:

```python
from oraclebook import Venue, Feed, Executor, Market
from oraclebook.core.bus import Bus

class KalshiVenue(Venue):
    name = "kalshi"
    def feed(self, bus: Bus) -> Feed: ...    # stream the venue's book/prices onto the bus
    def executor(self) -> Executor: ...      # place/cancel orders, report fills
    async def current_markets(self) -> list[Market]: ...
    def fee(self, price, size, is_maker) -> float: ...
```

Live in `oraclebook/adapters/kalshi/`. See `adapters/polymarket/` as the worked reference.

---

## The reference example (and an honest one)

The repo ships **`CompleteSetMaker`** — the real Polymarket complete-set market-maker this
framework grew out of — and its docstring is candid that **naive complete-set MM loses to
one-leg adverse selection**. The point of oraclebook isn't to hand you an edge; it's to let
you *find out cheaply* whether you have one — in paper and Replay — before you fund it.

## Next

- [NAMING.md](NAMING.md) — the full vocabulary + diagram
- [AGENTS.md](AGENTS.md) — extend oraclebook with Claude Code & AI coding agents

---

*oraclebook is research and infrastructure software, not financial advice or a promise of profit;
live prediction-market trading can lose money.*
