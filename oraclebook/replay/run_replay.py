"""run_replay — backtest a strategy by replaying a recording, deterministically.

Wires an App in replay mode (handlers run inline, nothing coalesced or dropped), a strict
ReplayClock, a ReplayFeed over the recording, a conservative book-aware PaperExecutor, and
a fresh Ledger; runs the strategy over the recorded order flow; optionally settles open
positions; and returns the App so you can read `app.ledger.fills()`, `app.book`, and the
paper balance. The same recording + strategy yields the same fills on every run.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable

from oraclebook.core.app import App
from oraclebook.core.book import PositionTracker
from oraclebook.core.clock import ReplayClock
from oraclebook.core.ledger import Ledger
from oraclebook.core.paper_executor import PaperExecutor
from oraclebook.replay.replay_feed import ReplayFeed


async def run_replay(
    recording_path: str,
    strategy,
    *,
    gates: list | None = None,
    starting_balance: float = 1000.0,
    ledger_path: str | None = None,
    fee_model: Callable[[float, float, bool], float] | None = None,
    latency_s: float = 0.0,
    settlements: dict[str, float] | None = None,
) -> App:
    """Replay `recording_path` through `strategy` in paper mode; return the finished App.

    `fee_model(price, size, is_maker)` charges a venue's fees (e.g. `PolymarketVenue().fee`);
    `latency_s` delays order activation; `settlements` maps token_id -> settlement value
    (1.0/0.0) to resolve positions still open at the end, so PnL is measured to resolution.
    """
    clock = ReplayClock()
    ledger = Ledger(
        ledger_path or os.path.join(tempfile.mkdtemp(), "replay_events.jsonl"), mode="w"
    )
    book = PositionTracker()
    executor = PaperExecutor(
        book,
        starting_balance=starting_balance,
        clock=clock,
        fee_model=fee_model,
        latency_s=latency_s,
    )
    app = App(
        strategy=strategy,
        executor=executor,
        book=book,
        gates=gates,
        clock=clock,
        ledger=ledger,
        replay=True,
    )
    await app.start()
    feed = ReplayFeed(app.bus, clock, recording_path)
    await feed.start()
    try:
        await feed.wait_done()
        for token_id, value in (settlements or {}).items():
            await app.resolve(token_id, value)
    finally:
        await feed.stop()
        await app.stop()
        ledger.close()
    return app
