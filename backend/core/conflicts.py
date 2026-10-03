"""Conflict detection from junction geometry (PRD Phase 6 Part A2).

Every approach (where a vehicle enters the junction) and every exit (where it
leaves) sits on a circle around the junction, at its compass bearing from the
junction centre. A movement is a chord from its approach point to its exit
point. Two movements conflict when:

- their chords CROSS (their endpoints interleave around the circle), or
- they MERGE: different approaches feeding the same exit.

Movements from the same approach never conflict with each other. U-turns
conflict with every movement from another approach. The only permissive
configuration is "free near-side turns": a near-side movement does not cross-
conflict with others, though merges are still checked (ADR-046).

Bearings are compass degrees (0 = north, clockwise). The test uses only the
circular order of endpoints, so it is independent of the map's coordinate
orientation, and it works for any junction shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from core.models import TurnType


class ArmGeometry(BaseModel):
    """An approach or exit on the junction circle."""

    model_config = ConfigDict(frozen=True)

    id: str
    bearing_deg: float = Field(ge=0, lt=360)


class MovementGeometry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    approach_id: str
    exit_id: str
    turn_type: TurnType


@dataclass(frozen=True)
class ConflictRules:
    free_near_side: bool = False
    uturn_conflicts_with_all: bool = True


def normalise_angle(degrees: float) -> float:
    return degrees % 360.0


def turn_type_for(
    approach_bearing: float,
    exit_bearing: float,
    driving_side: str,
    through_tolerance_deg: float,
    uturn_threshold_deg: float,
) -> TurnType:
    """Classify a movement from the bearings of its approach and exit.

    A vehicle arriving from bearing `b` travels towards `b + 180`. The signed
    change of heading is positive for a clockwise (right) turn and negative for
    an anticlockwise (left) turn."""
    heading_in = normalise_angle(approach_bearing + 180.0)
    change = (exit_bearing - heading_in + 540.0) % 360.0 - 180.0
    magnitude = abs(change)
    if magnitude <= through_tolerance_deg:
        return TurnType.THROUGH
    if magnitude >= uturn_threshold_deg:
        return TurnType.U_TURN
    is_left = change < 0
    if driving_side == "left":
        return TurnType.NEAR_SIDE if is_left else TurnType.FAR_SIDE
    return TurnType.NEAR_SIDE if not is_left else TurnType.FAR_SIDE


def _arc_position(bearing: float) -> float:
    return normalise_angle(bearing)


def chords_cross(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    """True when chords (a_start, a_end) and (b_start, b_end) interleave on a circle.

    Chords sharing an endpoint do not cross (they merge or diverge instead)."""
    points = [_arc_position(x) for x in (a_start, a_end, b_start, b_end)]
    if len({round(p, 9) for p in points}) < 4:
        return False
    lo, hi = sorted((points[0], points[1]))
    inside_b = [lo < p < hi for p in (points[2], points[3])]
    return inside_b[0] != inside_b[1]


def movement_conflict(
    a: MovementGeometry,
    b: MovementGeometry,
    arms: dict[str, ArmGeometry],
    rules: ConflictRules,
) -> bool:
    if a.id == b.id:
        return False
    if a.approach_id == b.approach_id:
        return False
    if a.exit_id == b.exit_id:
        return True
    if a.turn_type == TurnType.U_TURN or b.turn_type == TurnType.U_TURN:
        return rules.uturn_conflicts_with_all
    if rules.free_near_side and (
        a.turn_type == TurnType.NEAR_SIDE or b.turn_type == TurnType.NEAR_SIDE
    ):
        return False
    return chords_cross(
        arms[a.approach_id].bearing_deg,
        arms[a.exit_id].bearing_deg,
        arms[b.approach_id].bearing_deg,
        arms[b.exit_id].bearing_deg,
    )


def conflict_pairs(
    movements: list[MovementGeometry],
    arms: dict[str, ArmGeometry],
    rules: ConflictRules,
) -> list[tuple[str, str]]:
    """All conflicting pairs, as sorted (id, id) tuples in a deterministic order."""
    ordered = sorted(movements, key=lambda m: m.id)
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if movement_conflict(a, b, arms, rules):
                pairs.append((a.id, b.id))
    return pairs
