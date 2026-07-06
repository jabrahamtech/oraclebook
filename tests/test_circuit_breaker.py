"""CircuitBreaker: consecutive-loss streak (no time window) + latching force_trip."""

from oraclebook import CircuitBreaker, ReplayClock


def test_on_trip_fires_once_on_transition():
    """on_trip fires on the un-tripped→tripped transition, exactly once."""
    calls: list[str] = []
    cb = CircuitBreaker(on_trip=calls.append)
    cb.force_trip("manual")
    assert calls == ["manual"]
    cb.force_trip("again")  # already tripped → no second fire
    assert calls == ["manual"]


def test_on_trip_fires_on_loss_streak():
    """The hook fires for any trip cause, including a loss streak."""
    calls: list[str] = []
    cb = CircuitBreaker(max_consecutive_losses=2, on_trip=calls.append)
    cb.record_loss()
    assert calls == []
    cb.record_loss()  # hits the limit → trips → hook fires
    assert len(calls) == 1


def test_on_trip_hook_error_does_not_block_trip():
    """A throwing hook must not prevent the breaker from tripping."""
    def _boom(_reason: str) -> None:
        raise RuntimeError("hook failed")

    cb = CircuitBreaker(on_trip=_boom)
    cb.force_trip("manual")  # must not raise
    assert cb.is_tripped


def test_on_trip_fires_again_after_cooldown():
    """After cooldown elapses, a fresh trip re-fires the hook — the transition
    keys off effective is_tripped, not the raw flag."""
    clock = ReplayClock(start=1000.0)
    calls: list[str] = []
    cb = CircuitBreaker(cooldown_seconds=0.1, on_trip=calls.append, clock=clock)
    cb.force_trip("first")
    assert calls == ["first"]
    clock.advance_to(1000.15)  # past cooldown → is_tripped goes False
    assert not cb.is_tripped
    cb.force_trip("second")
    assert calls == ["first", "second"]


def test_not_tripped_initially():
    cb = CircuitBreaker()
    assert not cb.is_tripped
    assert cb._tripped is False
    assert cb.trip_reason == ""


def test_default_max_consecutive_losses_is_three():
    """Default trips at 3 losses (not 4 or 2)."""
    cb = CircuitBreaker()
    cb.record_loss()
    cb.record_loss()
    assert not cb.is_tripped
    cb.record_loss()
    assert cb.is_tripped


def test_loss_streak_survives_slow_resolution_cadence():
    """Losses arrive one per interval (minutes apart) — the streak must still
    accumulate. A time-windowed count made the kill threshold unreachable."""
    clock = ReplayClock(start=1000.0)
    cb = CircuitBreaker(max_consecutive_losses=3, clock=clock)
    cb.record_loss()
    clock.advance_to(1300.0)   # 5 minutes later
    cb.record_loss()
    clock.advance_to(1600.0)
    assert not cb.is_tripped
    cb.record_loss()
    assert cb.is_tripped


def test_latched_trip_survives_cooldown():
    """force_trip(latch=True) holds past cooldown and check_and_reset."""
    clock = ReplayClock(start=1000.0)
    cb = CircuitBreaker(cooldown_seconds=0.1, clock=clock)
    cb.force_trip("intraday_loss kill", latch=True)
    clock.advance_to(2000.0)  # far past cooldown
    assert cb.is_tripped
    assert "intraday_loss" in cb.trip_reason
    tripped, reason = cb.check_and_reset()
    assert tripped is True
    assert "intraday_loss" in reason


def test_win_resets_loss_streak():
    cb = CircuitBreaker(max_consecutive_losses=3)
    cb.record_loss()
    cb.record_loss()
    cb.record_win()  # reset
    cb.record_loss()
    assert not cb.is_tripped


def test_cooldown_resets():
    clock = ReplayClock(start=1000.0)
    cb = CircuitBreaker(max_consecutive_losses=1, cooldown_seconds=0.1, clock=clock)
    cb.record_loss()
    assert cb.is_tripped

    clock.advance_to(1000.15)
    # is_tripped is pure — returns False but doesn't reset internal state
    assert not cb.is_tripped
    assert cb._tripped is True

    tripped, reason = cb.check_and_reset()
    assert not tripped
    assert "losses" in reason
    assert cb._tripped is False


def test_force_trip():
    cb = CircuitBreaker()
    cb.force_trip("manual test")
    assert cb.is_tripped
    assert "manual test" in cb.trip_reason


def test_error_rate_trips():
    cb = CircuitBreaker(max_error_rate=0.5, error_window_seconds=300)
    cb.record_success()
    cb.record_success()
    cb.record_error()  # 1 err / 3 total = 33%
    cb.record_error()  # 2 err / 4 total = 50%, but < 5 events
    cb.record_error()  # 3 err / 5 total = 60% > 50%, trips
    assert cb.is_tripped


def test_check_and_reset_returns_reason():
    clock = ReplayClock(start=1000.0)
    cb = CircuitBreaker(max_consecutive_losses=1, cooldown_seconds=0.1, clock=clock)
    cb.record_loss()
    assert cb.is_tripped

    clock.advance_to(1000.15)
    tripped, reason = cb.check_and_reset()
    assert not tripped
    assert "1 losses" in reason
    assert cb.trip_reason == ""


def test_no_loss_window_param():
    """The old time-windowed loss_window_seconds param must not exist."""
    import inspect
    params = inspect.signature(CircuitBreaker.__init__).parameters
    assert "loss_window_seconds" not in params
