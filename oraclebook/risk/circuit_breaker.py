"""CircuitBreaker — an emergency stop that halts trading on rapid losses or errors.

Trips on a burst of losing trades or a high API error rate, then auto-resets after a
cooldown. The `on_trip` hook fires once on the un-tripped to tripped transition so a
consumer can react (e.g. cancel all resting orders). Ported unchanged from the
production bot — venue-agnostic; depends only on the Clock.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable

import structlog

from oraclebook.core.clock import RealClock
from oraclebook.core.protocols import Clock

logger = structlog.get_logger()


class CircuitBreaker:
    """Pauses trading when recent losses or errors breach configured thresholds."""

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        cooldown_seconds: float = 300,
        max_error_rate: float = 0.5,
        error_window_seconds: float = 60,
        on_trip: Callable[[str], None] | None = None,
        clock: Clock | None = None,
    ) -> None:
        # Fired once on the un-tripped -> tripped transition (any cause). Settable post-init.
        self.on_trip = on_trip
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_seconds = cooldown_seconds
        self.max_error_rate = max_error_rate
        self.error_window_seconds = error_window_seconds
        self._clock: Clock = clock or RealClock()

        # Consecutive-loss streak, cleared by a win. Deliberately NOT
        # time-windowed: resolutions arrive at most ~1 per interval per market,
        # so any window shorter than max_losses * interval makes the threshold
        # unreachable and the kill layer dead.
        self._loss_streak = 0
        self._recent_errors: deque[float] = deque()
        self._recent_successes: deque[float] = deque()
        self._tripped = False
        self._tripped_at: float = 0.0
        self._trip_reason: str = ""
        # A latched trip never auto-expires with the cooldown — it holds until
        # restart (the breaker deliberately starts clean each session). Used by
        # kills that demand human inspection (intraday loss, dead background task).
        self._latched = False

    @property
    def is_tripped(self) -> bool:
        """Currently tripped? Pure read — no side effects. False once cooldown elapses."""
        if not self._tripped:
            return False
        if self._latched:
            return True
        return self._clock.now() - self._tripped_at < self.cooldown_seconds

    @property
    def trip_reason(self) -> str:
        if not self._tripped:
            return ""
        if not self._latched and self._clock.now() - self._tripped_at >= self.cooldown_seconds:
            return ""
        return self._trip_reason

    def check_and_reset(self) -> tuple[bool, str]:
        """Tripped? If cooldown elapsed, reset and return (False, reason). Use on the
        execution path to resume trading after cooldown; use `is_tripped` for status.
        Latched trips never reset here — only a restart clears them."""
        if not self._tripped:
            return False, ""
        if self._latched:
            return True, self._trip_reason
        if self._clock.now() - self._tripped_at >= self.cooldown_seconds:
            reason = self._trip_reason
            self._reset()
            return False, reason
        return True, self._trip_reason

    def record_loss(self) -> None:
        self._loss_streak += 1
        if self._loss_streak >= self.max_consecutive_losses:
            self._trip(f"Hit {self._loss_streak} losses in a row")

    def record_win(self) -> None:
        """Record a winning trade — clears the loss streak."""
        self._loss_streak = 0

    def record_error(self) -> None:
        self._recent_errors.append(self._clock.now())
        self._cleanup_old(self._recent_errors, self.error_window_seconds)
        self._cleanup_old(self._recent_successes, self.error_window_seconds)
        total = len(self._recent_errors) + len(self._recent_successes)
        if total >= 5:
            rate = len(self._recent_errors) / total
            if rate > self.max_error_rate:
                self._trip(f"error rate {rate:.0%} exceeds {self.max_error_rate:.0%}")

    def record_success(self) -> None:
        self._recent_successes.append(self._clock.now())
        self._cleanup_old(self._recent_successes, self.error_window_seconds)

    def force_trip(self, reason: str, latch: bool = False) -> None:
        """Manually trip the breaker.

        latch=True holds the trip until restart instead of auto-expiring with
        the cooldown — for kills where auto-resuming means trading through a
        window of the fault while still bleeding (intraday loss, dead background
        task).
        """
        self._trip(reason, latch=latch)

    def _trip(self, reason: str, latch: bool = False) -> None:
        # Key off the EFFECTIVE state (is_tripped), not the raw flag: after cooldown
        # is_tripped is False while _tripped stays True until a check_and_reset(), so
        # keying off _tripped would suppress the hook on a fresh post-cooldown trip.
        was_tripped = self.is_tripped
        self._tripped = True
        self._tripped_at = self._clock.now()
        self._trip_reason = reason
        self._latched = self._latched or latch
        logger.warning(
            "circuit_breaker.tripped",
            reason=reason, cooldown=self.cooldown_seconds, latched=self._latched,
        )
        if not was_tripped and self.on_trip is not None:
            try:
                self.on_trip(reason)
            except Exception:
                logger.exception("circuit_breaker.on_trip_failed", reason=reason)

    def _reset(self) -> None:
        self._tripped = False
        self._trip_reason = ""
        self._loss_streak = 0
        self._recent_errors.clear()
        self._recent_successes.clear()
        logger.info("circuit_breaker.reset")

    def _cleanup_old(self, dq: deque[float], window: float) -> None:
        cutoff = self._clock.now() - window
        while dq and dq[0] < cutoff:
            dq.popleft()
