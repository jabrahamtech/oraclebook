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

import structlog

from oraclebook.core.bus import Bus
from oraclebook.core.clock import ReplayClock
from oraclebook.core.events import BookUpdate, IntervalBoundary, MarketsDiscovered, PriceTick
from oraclebook.core.models import Market

logger = structlog.get_logger()

# Map recorded type names back to event classes.
_TYPES = {
    "BookUpdate": BookUpdate,
    "PriceTick": PriceTick,
    "IntervalBoundary": IntervalBoundary,
    "MarketsDiscovered": MarketsDiscovered,
}


def _market(fields: dict) -> Market:
    return Market(**{
        **fields,
        "token_ids": tuple(fields["token_ids"]),
        "outcomes": tuple(fields.get("outcomes", ())),
    })


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
        """Await until every recorded event has been published; re-raise if replay failed."""
        await self._done.wait()
        if self._task is not None and self._task.done() and not self._task.cancelled():
            exc = self._task.exception()
            if exc is not None:
                raise exc

    async def _run(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            records: list[dict] = []
            for i, line in enumerate(lines):
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    if i < len(lines) - 1:
                        raise
                    # A recorder killed mid-write leaves a partial last line.
                    logger.warning("replay_feed.truncated_last_line", path=self._path)
            # Recorder assigns seq per topic as it consumes, so a burst across topics is
            # grouped by topic; event ts restores the order they were published in.
            records.sort(key=lambda r: (r["ts"], r["seq"]))
            for rec in records:
                cls = _TYPES.get(rec["type"])
                if cls is None:
                    continue
                payload = dict(rec["payload"])
                for key in ("bids", "asks"):
                    if key in payload:
                        payload[key] = tuple(tuple(level) for level in payload[key])
                if "markets" in payload:
                    payload["markets"] = tuple(_market(m) for m in payload["markets"])
                self._clock.advance_to(rec["ts"])
                await self._bus.publish(rec["topic"], cls(**payload))
                await asyncio.sleep(0)  # yield so consumers process before the next tick
        finally:
            self._done.set()
