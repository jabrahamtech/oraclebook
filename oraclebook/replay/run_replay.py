"""run_replay — backtest a strategy by replaying a recording (reproducible for a single feed).

Wires an App in replay mode (no coalescing/drops), a ReplayClock, a ReplayFeed over the
recording, and a Ledger; runs the strategy over the recorded order flow; drains; and
returns the App so you can read `app.ledger.fills()`, `app.book`, and the paper balance.

Reproducibility: for a single event stream (e.g. one venue's book feed) the same recording +
strategy yields the same fills every run. Recordings that mix multiple topics are replayed in
recorded `seq` order, but because topics are consumed on separate tasks the cross-topic
*interleaving* is best-effort, not byte-guaranteed — keep a backtest to one feed for strict
reproducibility.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

from oraclebook.core.app import App
from oraclebook.core.clock import ReplayClock
from oraclebook.core.ledger import Ledger
from oraclebook.replay.replay_feed import ReplayFeed


async def run_replay(
    recording_path: str,
    strategy,
    *,
    gates: list | None = None,
    starting_balance: float = 1000.0,
    ledger_path: str | None = None,
) -> App:
    """Replay `recording_path` through `strategy` in paper mode; return the finished App."""
    clock = ReplayClock(strict=False)
    ledger = Ledger(ledger_path or os.path.join(tempfile.mkdtemp(), "replay_events.jsonl"))
    app = App(
        strategy=strategy,
        executor="paper",
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
