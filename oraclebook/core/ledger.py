"""Ledger — the append-only event store and source of truth.

Every fill, resolution, and rejection is appended as one JSON line, so the Ledger is the
durable record you analyse for fills and PnL after a paper, live, or Replay run. JSONL keeps
it dependency-free and human-inspectable; swap in SQLite/Parquet
behind the same methods if you need scale. Implements `MetricsSink` + `ResolutionSink`,
so the App can record automatically.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from oraclebook.core.models import Fill, Resolution


def _jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses/enums/tuples into JSON-serialisable values."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


class Ledger:
    """Append-only JSONL event log. One file, one event per line.

    `mode="a"` (default) appends — right for a live account's durable record. Pass `mode="w"`
    for a backtest, so re-running into the same path doesn't double-count earlier runs."""

    def __init__(self, path: str = "events.jsonl", mode: str = "a") -> None:
        if mode not in ("a", "w"):
            raise ValueError(f"mode must be 'a' or 'w', got {mode!r}")
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._fh = open(path, mode, encoding="utf-8")

    def record(self, topic: str, obj: Any, ts: float | None = None) -> None:
        """Append one event: {topic, ts, payload}."""
        rec = {"topic": topic, "ts": ts if ts is not None else time.time(), "payload": _jsonable(obj)}
        self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self._fh.flush()

    # MetricsSink + ResolutionSink
    def record_fill(self, fill: Fill) -> None:
        self.record("fill", fill, getattr(fill, "ts", None))

    def record_resolution(self, resolution: Resolution) -> None:
        self.record("resolution", resolution, getattr(resolution, "ts", None))

    # read-back (for analysis / Replay verification)
    def events(self, topic: str | None = None) -> Iterator[dict]:
        """Iterate recorded events as dicts, optionally filtered by topic."""
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if topic is None or rec["topic"] == topic:
                    yield rec

    def fills(self) -> Iterator[dict]:
        yield from self.events("fill")

    def resolutions(self) -> Iterator[dict]:
        yield from self.events("resolution")

    def close(self) -> None:
        self._fh.close()
