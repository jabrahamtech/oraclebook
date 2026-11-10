"""run_replay — backtest a strategy by replaying a recording (reproducible for a single feed).

Wires an App in replay mode (no coalescing/drops), a ReplayClock, a ReplayFeed over the
recording, and a Ledger; runs the strategy over the recorded order flow; drains; and
returns the App so you can read `app.ledger.fills()`, `app.book`, and the paper balance.

Reproducibility: for a single event stream (e.g. one venue's book feed) the same recording +
strategy yields the same fills every run. Recordings that mix multiple topics are replayed in
recorded `seq` order, but because topics are consumed on separate tasks the cross-topic
*interleaving* is best-effort, not byte-guaranteed — keep a backtest to one feed for strict
reproducibility.

Fill models: `"instant"` (default) fills every order at its limit price for full size;
`"depth"` uses `DepthPaperExecutor` — walks the recorded depth, applies `latency_ms`, fills
partially, and charges `fee(price, size, is_maker)` (e.g. a Venue's `fee`).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Literal

from oraclebook.core.app import App
from oraclebook.core.book import PositionTracker
from oraclebook.core.clock import ReplayClock
from oraclebook.core.depth_paper_executor import DepthPaperExecutor, FeeFn
from oraclebook.core.ledger import Ledger
from oraclebook.core.protocols import Executor
from oraclebook.replay.replay_feed import ReplayFeed


async def run_replay(
    recording_path: str,
    strategy,
    *,
    gates: list | None = None,
    starting_balance: float = 1000.0,
    ledger_path: str | None = None,
    fill_model: Literal["instant", "depth"] = "instant",
    latency_ms: float = 0.0,
    fee: FeeFn | None = None,
) -> App:
    """Replay `recording_path` through `strategy` in paper mode; return the finished App."""
    clock = ReplayClock(strict=False)
    ledger = Ledger(ledger_path or os.path.join(tempfile.mkdtemp(), "replay_events.jsonl"))
    book = PositionTracker()
    executor: Executor | str
    if fill_model == "depth":
        executor = DepthPaperExecutor(
            book, starting_balance, latency_ms=latency_ms, fee=fee, clock=clock
        )
    elif fill_model == "instant":
        if latency_ms or fee is not None:
            raise ValueError("latency_ms and fee require fill_model='depth'")
        executor = "paper"
    else:
        raise ValueError(f"unknown fill_model: {fill_model!r}")
    app = App(
        strategy=strategy,
        executor=executor,
        book=book,
        gates=gates,
        clock=clock,
        ledger=ledger,
        starting_balance=starting_balance,
        replay=True,
    )
    await app.start()
    feed = ReplayFeed(app.bus, clock, recording_path)
    await feed.start()
    await feed.wait_done()
    # Drain: let the consumers finish processing every queued event before stopping.
    for _ in range(10_000):
        await asyncio.sleep(0)
        if not any(d for depths in app.bus.queue_depths.values() for d in depths):
            break
    await asyncio.sleep(0.01)
    await feed.stop()
    await app.stop()
    ledger.close()
    return app
