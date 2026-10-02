import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from core.models import (
    Command,
    CommandType,
    ConflictMatrix,
    Direction,
    SensorReading,
    build_idempotency_key,
)

MOVEMENTS = [Direction.N, Direction.S, Direction.E, Direction.W]


def _ns_ew_matrix() -> ConflictMatrix:
    return ConflictMatrix(
        movements=MOVEMENTS,
        conflict_pairs=[
            (Direction.N, Direction.E),
            (Direction.N, Direction.W),
            (Direction.S, Direction.E),
            (Direction.S, Direction.W),
        ],
    )


def test_conflicts_is_symmetric_regardless_of_stored_order() -> None:
    matrix = _ns_ew_matrix()
    assert matrix.conflicts(Direction.N, Direction.E)
    assert matrix.conflicts(Direction.E, Direction.N)  # reverse order, not authored directly


def test_non_conflicting_pair() -> None:
    matrix = _ns_ew_matrix()
    assert not matrix.conflicts(Direction.N, Direction.S)
    assert not matrix.conflicts(Direction.E, Direction.W)


@given(st.sampled_from(MOVEMENTS), st.sampled_from(MOVEMENTS))
def test_conflicts_symmetry_property(a: Direction, b: Direction) -> None:
    matrix = _ns_ew_matrix()
    assert matrix.conflicts(a, b) == matrix.conflicts(b, a)


def test_is_compatible_true_for_ns_pair() -> None:
    matrix = _ns_ew_matrix()
    assert matrix.is_compatible({Direction.N, Direction.S})


def test_is_compatible_false_for_conflicting_pair() -> None:
    matrix = _ns_ew_matrix()
    assert not matrix.is_compatible({Direction.N, Direction.E})


def test_is_compatible_single_movement() -> None:
    matrix = _ns_ew_matrix()
    assert matrix.is_compatible({Direction.N})


def test_idempotency_key_format() -> None:
    key = build_idempotency_key(plan_version=3, intersection_id="C4", seq=7)
    assert key == "v3:C4:0007"


def test_command_idempotency_key_matches_builder() -> None:
    key = build_idempotency_key(1, "A1", 1)
    cmd = Command(
        id="cmd-1",
        type=CommandType.PREEMPT,
        intersection_id="A1",
        plan_version=1,
        idempotency_key=key,
        created_at=0,
    )
    assert cmd.idempotency_key == "v1:A1:0001"


def test_command_is_frozen() -> None:
    cmd = Command(
        id="cmd-1",
        type=CommandType.PREEMPT,
        intersection_id="A1",
        plan_version=1,
        idempotency_key="v1:A1:0001",
        created_at=0,
    )
    with pytest.raises(ValidationError):
        cmd.plan_version = 2  # type: ignore[misc]


def test_sensor_reading_is_frozen() -> None:
    reading = SensorReading(
        source_id="sensor-1", road_id="A1->A2", value=0.4, timestamp=0, sequence=1
    )
    with pytest.raises(ValidationError):
        reading.value = 0.9  # type: ignore[misc]
