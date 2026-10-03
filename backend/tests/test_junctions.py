import pytest
from pydantic import ValidationError

from core.conflicts import ConflictRules
from core.junctions import (
    Approach,
    Exit,
    Junction,
    JunctionConflictMatrix,
    JunctionPhase,
    Movement,
)
from core.models import IntersectionKind, TurnType


def _four_way() -> Junction:
    bearings = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}
    approaches = tuple(
        Approach(id=f"{d}-in", intersection_id="J", segment_id=f"{d}->J", bearing_deg=b, lanes=2)
        for d, b in bearings.items()
    )
    exits = tuple(
        Exit(id=f"{d}-out", intersection_id="J", segment_id=f"J->{d}", bearing_deg=b)
        for d, b in bearings.items()
    )
    movements = []
    for d, b in bearings.items():
        for e, eb in bearings.items():
            if e == d:
                continue
            movements.append(
                Movement(
                    id=f"{d}-to-{e}",
                    intersection_id="J",
                    approach_id=f"{d}-in",
                    exit_id=f"{e}-out",
                    turn_type=TurnType.THROUGH
                    if abs((eb - (b + 180)) % 360 - 180) < 30
                    else TurnType.NEAR_SIDE,
                )
            )
    return Junction(
        id="J",
        kind=IntersectionKind.SIGNALISED,
        x=0.0,
        y=0.0,
        approaches=approaches,
        exits=exits,
        movements=tuple(movements),
    )


def test_junction_conflict_matrix_is_symmetric_and_irreflexive():
    matrix = _four_way().conflict_matrix(ConflictRules())
    for a in matrix.movement_ids:
        assert not matrix.conflicts(a, a)
        for b in matrix.movement_ids:
            assert matrix.conflicts(a, b) == matrix.conflicts(b, a)


def test_opposing_through_movements_are_compatible_and_crossing_ones_are_not():
    matrix = _four_way().conflict_matrix(ConflictRules())
    assert matrix.is_compatible(["N-to-S", "S-to-N"])
    assert not matrix.is_compatible(["N-to-S", "E-to-W"])


def test_matrix_pairs_are_deterministic():
    first = _four_way().conflict_matrix(ConflictRules())
    second = _four_way().conflict_matrix(ConflictRules())
    assert first == second


def test_phase_movements_must_be_sorted_and_unique():
    with pytest.raises(ValidationError, match="sorted"):
        JunctionPhase(
            id="p",
            intersection_id="J",
            movement_ids=("b", "a"),
            min_green_ms=1000,
            configured_green_ms=1000,
            amber_ms=1000,
            all_red_ms=1000,
        )
    with pytest.raises(ValidationError, match="twice"):
        JunctionPhase(
            id="p",
            intersection_id="J",
            movement_ids=("a", "a"),
            min_green_ms=1000,
            configured_green_ms=1000,
            amber_ms=1000,
            all_red_ms=1000,
        )


def test_configured_green_below_the_floor_is_rejected():
    with pytest.raises(ValidationError, match="shorter than"):
        JunctionPhase(
            id="p",
            intersection_id="J",
            movement_ids=("a",),
            min_green_ms=2000,
            configured_green_ms=1000,
            amber_ms=1000,
            all_red_ms=1000,
        )


def test_conflict_matrix_rejects_self_conflicts_and_unknown_ids():
    with pytest.raises(ValidationError):
        JunctionConflictMatrix(movement_ids=("a", "b"), pairs=(("a", "a"),))
    with pytest.raises(ValidationError):
        JunctionConflictMatrix(movement_ids=("a", "b"), pairs=(("a", "c"),))


def test_conflict_matrix_rejects_unsorted_pairs():
    with pytest.raises(ValidationError):
        JunctionConflictMatrix(movement_ids=("a", "b"), pairs=(("b", "a"),))
