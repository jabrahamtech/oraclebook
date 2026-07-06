"""Clock: RealClock advances; ReplayClock is monotonic (strict rejects rewind)."""
import pytest

from oraclebook import RealClock, ReplayClock


def test_real_clock_is_positive():
    assert RealClock().now() > 0


def test_replay_clock_starts_at_zero_and_advances():
    c = ReplayClock()
    assert c.now() == 0.0
    c.advance_to(100.0)
    assert c.now() == 100.0


def test_replay_clock_strict_rejects_rewind():
    c = ReplayClock(strict=True)
    c.advance_to(100.0)
    with pytest.raises(RuntimeError):
        c.advance_to(50.0)


def test_replay_clock_nonstrict_ignores_rewind():
    c = ReplayClock(strict=False)
    c.advance_to(100.0)
    c.advance_to(50.0)
    assert c.now() == 100.0
