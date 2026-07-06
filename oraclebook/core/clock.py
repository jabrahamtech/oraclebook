"""Clock implementations — the time source for the decision path.

`RealClock` in production; `ReplayClock` in **Replay** (advanced by the replay
driver per event, so a single-feed replay is reproducible). Both satisfy the `Clock`
protocol in `oraclebook.core.protocols` structurally — anything on the
strategy / risk / execution path reads `self._clock.now()`, never `time.time()`,
so the same code runs live and in replay unchanged.
"""
from __future__ import annotations

import time


class RealClock:
    """Wall-clock — the production default."""

    __slots__ = ()

    def now(self) -> float:
        return time.time()


class ReplayClock:
    """Virtual clock driven by the replay driver. Monotonic: `advance_to` rejects
    rewind in strict mode so tests catch any attempt to move time backwards."""

    __slots__ = ("_t", "_strict")

    def __init__(self, start: float = 0.0, *, strict: bool = True) -> None:
        self._t = start
        self._strict = strict

    def now(self) -> float:
        return self._t

    def advance_to(self, ts: float) -> None:
        if ts < self._t:
            if self._strict:
                raise RuntimeError(f"ReplayClock cannot rewind: current={self._t} requested={ts}")
            return
        self._t = ts


DEFAULT_CLOCK = RealClock()
