"""Recorder — capture live feed events to a recording for Replay.

Registers an inline bus handler on the market-data topics, so every event is captured in
exact publish order with no queue to overflow, and appends each as one JSON line:
`{seq, topic, ts, type, payload}`. `seq` is assigned at publish time and continues from an
existing file, so a resumed recording replays in order. Lines are flushed at least every
`flush_interval_s`, bounding what a crash can lose. Replay reads this back in `seq` order,
so you backtest on the recorded order books that actually happened.
"""
from __future__ import annotations

import json
import os
import time

import structlog

from oraclebook.core.bus import Bus
from oraclebook.core.events import (
    EVT_BOOK_UPDATE,
    EVT_INTERVAL_BOUNDARY,
    EVT_PRICE_TICK,
)
from oraclebook.core.ledger import _jsonable

logger = structlog.get_logger()

DEFAULT_TOPICS = (EVT_BOOK_UPDATE, EVT_PRICE_TICK, EVT_INTERVAL_BOUNDARY)


def _next_seq(path: str) -> int:
    """One past the highest `seq` already in `path` (0 for a new file)."""
    if not os.path.exists(path):
        return 0
    last = -1
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                last = max(last, json.loads(line)["seq"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return last + 1


class Recorder:
    """Writes every feed event on the recorded topics to a JSONL recording file."""

    def __init__(
        self,
        bus: Bus,
        path: str,
        topics: tuple[str, ...] = DEFAULT_TOPICS,
        flush_interval_s: float = 1.0,
    ) -> None:
        self._bus = bus
        self._path = path
        self._topics = topics
        self._flush_interval_s = flush_interval_s
        self._running = False
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._seq = _next_seq(path)
        self._fh = open(path, "a", encoding="utf-8")
        self._last_flush = time.monotonic()

    async def start(self) -> None:
        self._running = True
        for topic in self._topics:
            self._bus.add_handler(topic, self._writer(topic))

    async def stop(self) -> None:
        self._running = False
        if not self._fh.closed:
            self._fh.flush()
            self._fh.close()

    def _writer(self, topic: str):
        async def write(event) -> None:
            if not self._running:
                return
            rec = {
                "seq": self._seq,
                "topic": topic,
                "ts": getattr(event, "ts", 0.0),
                "type": type(event).__name__,
                "payload": _jsonable(event),
            }
            self._seq += 1
            self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            now = time.monotonic()
            if now - self._last_flush >= self._flush_interval_s:
                self._fh.flush()
                self._last_flush = now

        return write
