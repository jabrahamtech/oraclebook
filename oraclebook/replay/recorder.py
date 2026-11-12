"""Recorder — capture live feed events to a recording for Replay.

Subscribes *raw* (no coalescing, so every individual event is kept, subject to the bus
queue bound) to the market-data topics and appends each as one JSON line:
`{seq, topic, ts, type, payload}`. Replay reads this back and re-publishes it in `seq`
order, so you backtest on the recorded order books that actually happened — the headline
oraclebook feature. Reproducible for a single feed; cross-topic interleaving is best-effort.
"""
from __future__ import annotations

import asyncio
import json
import os

from oraclebook.core.bus import Bus
from oraclebook.core.events import (
    EVT_BOOK_UPDATE,
    EVT_INTERVAL_BOUNDARY,
    EVT_MARKETS_DISCOVERED,
    EVT_PRICE_TICK,
)
from oraclebook.core.ledger import _jsonable

DEFAULT_TOPICS = (
    EVT_BOOK_UPDATE, EVT_PRICE_TICK, EVT_INTERVAL_BOUNDARY, EVT_MARKETS_DISCOVERED
)


class Recorder:
    """Writes every feed event on the recorded topics to a JSONL recording file."""

    def __init__(self, bus: Bus, path: str, topics: tuple[str, ...] = DEFAULT_TOPICS) -> None:
        self._bus = bus
        self._path = path
        self._topics = topics
        self._seq = _next_seq(path)
        self._tasks: list[asyncio.Task] = []
        self._subs: list[tuple[str, asyncio.Queue]] = []
        self._running = False
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    async def start(self) -> None:
        self._running = True
        for topic in self._topics:
            sub = self._bus.subscribe_raw(topic)
            self._subs.append((topic, sub))
            self._tasks.append(asyncio.create_task(self._consume(topic, sub)))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        # Write whatever was published but not yet consumed, so the tail isn't lost.
        for topic, sub in self._subs:
            while not sub.empty():
                self._write(topic, sub.get_nowait())
        self._fh.flush()
        self._fh.close()

    async def _consume(self, topic: str, sub) -> None:
        async for event in Bus.consume(sub, running=lambda: self._running):
            self._write(topic, event)

    def _write(self, topic: str, event) -> None:
        rec = {
            "seq": self._seq,
            "topic": topic,
            "ts": getattr(event, "ts", 0.0),
            "type": type(event).__name__,
            "payload": _jsonable(event),
        }
        self._seq += 1
        self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")


def _next_seq(path: str) -> int:
    """Continue seq after an existing recording, so appended sessions replay in order."""
    last = -1
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    last = max(last, int(json.loads(line)["seq"]))
                except (ValueError, KeyError, TypeError):
                    continue
    return last + 1
