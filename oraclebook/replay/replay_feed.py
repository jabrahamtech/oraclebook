"""ReplayFeed — re-publish a recording onto the bus in order, on a virtual clock.

Reads a JSONL recording, reconstructs each event, advances the `ReplayClock` to the
event's recorded timestamp, and publishes it — so a strategy re-runs against the exact
recorded order books, reproducibly for a single feed (cross-topic interleaving is
best-effort; fills are simulated by the paper executor). Implements the `Feed` protocol,
so the App drives it like any live feed (with `replay=True` so nothing is coalesced or dropped).
"""
from __future__ import annotations

import asyncio
import json

from oraclebook.core.bus import Bus
from oraclebook.core.clock import ReplayClock
from oraclebook.core.events import BookUpdate, IntervalBoundary, PriceTick

# Map recorded type names back to event classes.
_TYPES = {
    "BookUpdate": BookUpdate,
    "PriceTick": PriceTick,
    "IntervalBoundary": IntervalBoundary,
}


class ReplayFeed:
    """Replays a recorded JSONL file onto the bus in seq order on a ReplayClock."""

    def __init__(self, bus: Bus, clock: ReplayClock, path: str) -> None:
        self._bus = bus
        self._clock = clock
        self._path = path
        self._task: asyncio.Task | None = None
        self._done = asyncio.Event()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def wait_done(self) -> None:
        """Await until every recorded event has been published."""
        await self._done.wait()

    async def _run(self) -> None:
        records: list[dict] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        records.sort(key=lambda r: r["seq"])
        for rec in records:
            cls = _TYPES.get(rec["type"])
            if cls is None:
                continue
            self._clock.advance_to(rec["ts"])
            await self._bus.publish(rec["topic"], cls(**rec["payload"]))
            await asyncio.sleep(0)  # yield so consumers process before the next tick
        self._done.set()
