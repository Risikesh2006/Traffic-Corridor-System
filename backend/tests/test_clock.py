import pytest

from core.clock import SimClock


def test_starts_at_zero() -> None:
    clock = SimClock()
    assert clock.now() == 0


def test_advance_moves_time_forward() -> None:
    clock = SimClock()
    clock.advance(500)
    assert clock.now() == 500
    clock.advance(250)
    assert clock.now() == 750


def test_advance_rejects_negative() -> None:
    clock = SimClock()
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_now_seconds() -> None:
    clock = SimClock()
    clock.advance(1500)
    assert clock.now_seconds() == 1.5


def test_format_at_start() -> None:
    clock = SimClock(start_wall_label="14:00:00")
    assert clock.format() == "14:00:00.000"


def test_format_after_advance() -> None:
    clock = SimClock(start_wall_label="14:00:00")
    clock.advance(61_234)
    assert clock.format() == "14:01:01.234"


def test_format_wraps_past_midnight() -> None:
    clock = SimClock(start_wall_label="23:59:59")
    clock.advance(2_000)
    assert clock.format() == "00:00:01.000"


def test_format_explicit_timestamp_independent_of_current_time() -> None:
    clock = SimClock(start_wall_label="14:00:00")
    clock.advance(5_000)
    assert clock.format(1_000) == "14:00:01.000"
