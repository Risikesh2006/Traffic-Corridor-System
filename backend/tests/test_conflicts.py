from hypothesis import given
from hypothesis import strategies as st

from core.conflicts import (
    ArmGeometry,
    ConflictRules,
    MovementGeometry,
    TurnType,
    chords_cross,
    conflict_pairs,
    movement_conflict,
    turn_type_for,
)

RULES = ConflictRules()


def _arms(**bearings: float) -> dict[str, ArmGeometry]:
    return {k: ArmGeometry(id=k, bearing_deg=v) for k, v in bearings.items()}


def _mv(mid: str, approach: str, exit_: str, turn: TurnType = TurnType.THROUGH) -> MovementGeometry:
    return MovementGeometry(id=mid, approach_id=approach, exit_id=exit_, turn_type=turn)


def _turned(mid: str, approach: str, exit_: str, arms: dict[str, ArmGeometry]) -> MovementGeometry:
    turn = turn_type_for(
        arms[approach].bearing_deg, arms[exit_].bearing_deg, "left", 30, 150
    )
    return _mv(mid, approach, exit_, turn)


def _four_way() -> tuple[dict[str, ArmGeometry], list[MovementGeometry]]:
    arms = _arms(N=0, E=90, S=180, W=270)
    pairs = [
        ("N-S", "N", "S"), ("N-E", "N", "E"), ("N-W", "N", "W"),
        ("S-N", "S", "N"), ("S-W", "S", "W"), ("S-E", "S", "E"),
        ("E-W", "E", "W"), ("E-S", "E", "S"), ("E-N", "E", "N"),
        ("W-E", "W", "E"), ("W-N", "W", "N"), ("W-S", "W", "S"),
    ]
    return arms, [_turned(mid, a, e, arms) for mid, a, e in pairs]


def _pair(pairs: list[tuple[str, str]], a: str, b: str) -> bool:
    return (a, b) in pairs or (b, a) in pairs


def test_opposing_through_movements_do_not_conflict_on_a_four_way():
    arms, movements = _four_way()
    pairs = conflict_pairs(movements, arms, RULES)
    assert not _pair(pairs, "N-S", "S-N")
    assert not _pair(pairs, "E-W", "W-E")


def test_crossing_through_movements_conflict_on_a_four_way():
    arms, movements = _four_way()
    pairs = conflict_pairs(movements, arms, RULES)
    assert _pair(pairs, "N-S", "E-W")
    assert _pair(pairs, "N-S", "W-E")


def test_movements_from_the_same_approach_never_conflict():
    arms, movements = _four_way()
    pairs = conflict_pairs(movements, arms, RULES)
    assert not _pair(pairs, "N-S", "N-E")
    assert not _pair(pairs, "N-E", "N-W")


def test_two_approaches_feeding_the_same_exit_merge_and_conflict():
    arms = _arms(N=0, E=90, S=180, W=270)
    a = _mv("N-W", "N", "W")
    b = _mv("S-W", "S", "W")
    assert movement_conflict(a, b, arms, RULES)


def test_free_near_side_removes_crossing_but_keeps_merges():
    arms = _arms(X=10, Y=100, Z=200, W=300)
    near = _mv("near", "X", "Z", TurnType.NEAR_SIDE)
    crossing = _mv("cross", "Y", "W")
    merge = _mv("merge", "W", "Z")
    assert movement_conflict(near, crossing, arms, RULES)
    free = ConflictRules(free_near_side=True)
    assert not movement_conflict(near, crossing, arms, free)
    assert movement_conflict(near, merge, arms, free)


def test_uturn_conflicts_with_every_other_approach_by_default():
    arms = _arms(N=0, E=90, S=180)
    uturn = _mv("N-N", "N", "N", TurnType.U_TURN)
    other = _mv("E-S", "E", "S")
    assert movement_conflict(uturn, other, arms, RULES)


def test_uturn_allowed_when_configured():
    arms = _arms(N=0, E=90, S=180)
    uturn = _mv("N-N", "N", "N", TurnType.U_TURN)
    other = _mv("E-S", "E", "S")
    lenient = ConflictRules(uturn_conflicts_with_all=False)
    assert not movement_conflict(uturn, other, arms, lenient)


def test_t_junction_three_arms_has_conflicts_only_between_crossing_movements():
    arms = _arms(N=0, E=90, S=180)
    movements = [
        _mv("N-S", "N", "S"),
        _mv("N-E", "N", "E", TurnType.FAR_SIDE),
        _mv("E-N", "E", "N", TurnType.NEAR_SIDE),
        _mv("E-S", "E", "S", TurnType.FAR_SIDE),
        _mv("S-N", "S", "N"),
        _mv("S-E", "S", "E", TurnType.NEAR_SIDE),
    ]
    pairs = conflict_pairs(movements, arms, RULES)
    assert not _pair(pairs, "N-S", "S-N")
    assert _pair(pairs, "N-E", "S-E")
    assert pairs == sorted(pairs)


def test_y_junction_merges_onto_a_shared_exit():
    arms = _arms(N=0, SE=135, SW=225)
    movements = [
        _mv("N-SE", "N", "SE", TurnType.FAR_SIDE),
        _mv("SW-SE", "SW", "SE", TurnType.NEAR_SIDE),
        _mv("SE-N", "SE", "N"),
    ]
    pairs = conflict_pairs(movements, arms, RULES)
    assert _pair(pairs, "N-SE", "SW-SE")
    assert not _pair(pairs, "N-SE", "SE-N")


def test_five_way_junction_crossings_are_geometric():
    arms = _arms(N=0, NE=72, SE=144, SW=216, NW=288)
    through = [
        _mv("N-SW", "N", "SW"),
        _mv("NE-NW", "NE", "NW"),
        _mv("SE-N", "SE", "N"),
    ]
    pairs = conflict_pairs(through, arms, RULES)
    assert _pair(pairs, "N-SW", "NE-NW")
    assert not _pair(pairs, "N-SW", "SE-N")


def test_skewed_four_way_still_has_opposing_pairs_non_conflicting():
    arms = _arms(N=10, E=95, S=185, W=260)
    movements = [_mv("N-S", "N", "S"), _mv("S-N", "S", "N"), _mv("E-W", "E", "W"), _mv("W-E", "W", "E")]
    pairs = conflict_pairs(movements, arms, RULES)
    assert not _pair(pairs, "N-S", "S-N")
    assert _pair(pairs, "N-S", "E-W")


def test_turn_type_follows_driving_side():
    through = turn_type_for(0, 180, "left", 30, 150)
    assert through == TurnType.THROUGH
    turn_to_east = turn_type_for(0, 90, "left", 30, 150)
    turn_to_west = turn_type_for(0, 270, "left", 30, 150)
    assert turn_to_east == TurnType.NEAR_SIDE
    assert turn_to_west == TurnType.FAR_SIDE
    assert turn_type_for(0, 270, "right", 30, 150) == TurnType.NEAR_SIDE
    assert turn_type_for(0, 0, "left", 30, 150) == TurnType.U_TURN


def test_chord_crossing_is_symmetric_in_chord_order():
    assert chords_cross(0, 180, 90, 270) is True
    assert chords_cross(90, 270, 0, 180) is True


def test_chords_sharing_an_endpoint_do_not_cross():
    assert chords_cross(0, 180, 0, 90) is False


@given(
    a0=st.floats(min_value=0, max_value=359.9),
    a1=st.floats(min_value=0, max_value=359.9),
    b0=st.floats(min_value=0, max_value=359.9),
    b1=st.floats(min_value=0, max_value=359.9),
)
def test_chord_crossing_is_symmetric_and_irreflexive(a0: float, a1: float, b0: float, b1: float) -> None:
    assert chords_cross(a0, a1, b0, b1) == chords_cross(b0, b1, a0, a1)
    assert not chords_cross(a0, a1, a0, a1)


@given(
    bearings=st.lists(st.floats(min_value=0, max_value=359.0), min_size=3, max_size=6, unique=True)
)
def test_random_junction_conflict_matrix_is_symmetric_and_irreflexive(bearings: list[float]) -> None:
    arms = {f"A{i}": ArmGeometry(id=f"A{i}", bearing_deg=b) for i, b in enumerate(bearings)}
    ids = sorted(arms)
    movements = [
        _mv(f"{a}-{b}", a, b)
        for a in ids
        for b in ids
        if a != b
    ]
    for m in movements:
        assert not movement_conflict(m, m, arms, RULES)
    for a in movements:
        for b in movements:
            assert movement_conflict(a, b, arms, RULES) == movement_conflict(b, a, arms, RULES)
