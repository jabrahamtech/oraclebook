"""ReplayFeed — re-publish a recording onto the bus in order, on a virtual clock.

Reads a JSONL recording, reconstructs each event, advances the `ReplayClock` to the
event's recorded timestamp, and publishes it. With an App in replay mode, handlers run
inline on publish, so each event is fully processed (strategy, gates, simulated fills)
before the clock moves to the next one — deterministic and free of lookahead across topics.
Implements the `Feed` protocol, so the App drives it like any live feed.
"""
from __future__ import annotations

import asyncio
import json

import structlog

from oraclebook.core.bus import Bus
from oraclebook.core.clock import ReplayClock
from oraclebook.core.events import BookUpdate, IntervalBoundary, PriceTick

logger = structlog.get_logger()

# Map recorded type names back to event classes.
_TYPES = {
    "BookUpdate": BookUpdate,
    "PriceTick": PriceTick,
    "IntervalBoundary": IntervalBoundary,
}


def _restore(cls: type, payload: dict):
    """Rebuild a frozen event, turning JSON lists back into the tuples it was recorded with."""
    fields = {
        k: tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list) else v
        for k, v in payload.items()
    }
    return cls(**fields)


def load_recording(path: str) -> list[dict]:
    """Read a recording in (seq) order. A malformed line — typically the partial last line of
    a recording cut off by a crash — is skipped with a warning rather than aborting."""
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("replay.bad_line_skipped", path=path, line=lineno)
    records.sort(key=lambda r: r["seq"])
    return records


class ReplayFeed:
    """Replays a recorded JSONL file onto the bus in seq order on a ReplayClock."""

    def __init__(self, bus: Bus, clock: ReplayClock, path: str) -> None:
        self._bus = bus
        self._clock = clock
        self._path = path
        self._task: asyncio.Task | None = None
        self._done = asyncio.Event()
        self._error: BaseException | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def wait_done(self) -> None:
        """Await until every recorded event has been published and handled; re-raises a
        failure in the replay loop instead of hanging."""
        await self._done.wait()
        if self._error is not None:
            raise self._error

    async def _run(self) -> None:
        try:
            for rec in load_recording(self._path):
                cls = _TYPES.get(rec["type"])
                if cls is None:
                    continue
                self._clock.advance_to(rec["ts"])
                await self._bus.publish(rec["topic"], _restore(cls, rec["payload"]))
                await asyncio.sleep(0)
        except Exception as exc:  # noqa: BLE001 — re-raised from wait_done()
            self._error = exc
        finally:
            self._done.set()
