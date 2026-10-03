"""Temporary bridge from the general junction model to the old N/S/E/W model
(PRD Phase 6 Stage 6.1.5, ADR-047, ADR-050).

Lets Stage 6.2 migrate consumers (`controller`, `safety`, `chaos`, `sim`,
`state`, `tracking`, `routing`) one at a time off `core.models.City` while
their data source is already the general model. Deleted once every consumer
reads `GeneralCity` directly (the "contract" step).

Only cardinal (compass) junctions can be adapted, because the old model's
`Approach.direction` is one of N/S/E/W. The grid is cardinal by construction;
a real imported city is not, and will never go through this adapter.

IMPORTANT — this adapter does NOT claim to reproduce the hand-authored grid
conflict matrix bit-for-bit. See ADR-050: the general model finds conflicts
the old grid's authored matrix misses (two approaches merging into the same
exit road), so the adapted matrix is a strict superset of the old one. The
equivalence test checks every structural fact (nodes, segments, hospitals,
served movements, entry node) and checks no old conflict is lost — not that
the two matrices are identical, because they must not be.
"""

from __future__ import annotations

from core.config import Settings
from core.conflicts import ConflictRules
from core.general_city import GeneralCity, GeneralRoadSegment
from core.junctions import Junction, Movement
from core.models import (
    Approach,
    City,
    ConflictMatrix,
    Direction,
    Hospital,
    Intersection,
    IntersectionKind,
    Phase,
    RoadSegment,
    SignalPlanEntry,
)

_BEARING_TO_DIRECTION = {
    0.0: Direction.N,
    90.0: Direction.E,
    180.0: Direction.S,
    270.0: Direction.W,
}


class LegacyAdapterError(ValueError):
    pass


def _direction_of_bearing(bearing_deg: float) -> Direction:
    try:
        return _BEARING_TO_DIRECTION[bearing_deg]
    except KeyError as exc:
        raise LegacyAdapterError(
            f"bearing {bearing_deg} is not cardinal (0/90/180/270); only a grid city can "
            "be adapted to the old model"
        ) from exc


def _direction_of_approach(junction: Junction, approach_id: str) -> Direction:
    approach = next(a for a in junction.approaches if a.id == approach_id)
    return _direction_of_bearing(approach.bearing_deg)


def _movements_by_direction(junction: Junction) -> dict[Direction, list[Movement]]:
    grouped: dict[Direction, list[Movement]] = {}
    for movement in junction.movements:
        direction = _direction_of_approach(junction, movement.approach_id)
        grouped.setdefault(direction, []).append(movement)
    return grouped


def _adapt_conflict_matrix(junction: Junction, settings: Settings) -> ConflictMatrix:
    grouped = _movements_by_direction(junction)
    matrix = junction.conflict_matrix(ConflictRules())
    directions = sorted(grouped, key=lambda d: d.value)
    pairs: list[tuple[Direction, Direction]] = []
    for i, d1 in enumerate(directions):
        for d2 in directions[i + 1 :]:
            if any(matrix.conflicts(m1.id, m2.id) for m1 in grouped[d1] for m2 in grouped[d2]):
                pairs.append((d1, d2))
                pairs.append((d2, d1))
    return ConflictMatrix(movements=list(directions), conflict_pairs=pairs)


def _adapt_phases(
    junction: Junction, settings: Settings
) -> tuple[list[Phase], list[SignalPlanEntry]]:
    """One old-style phase per compass direction, never paired.

    Pairing opposing directions (as the old grid's NS_THROUGH/EW_THROUGH
    does) assumes every movement of one direction is compatible with every
    movement of the other. ADR-050 found this false in general: a far-side
    turn from one approach can merge with a near-side turn from the
    opposing approach. A single-direction phase is always conflict-free,
    because movements from the same approach never conflict (ADR-046), so
    this is the only grouping that is safe without re-deriving which
    specific movement PAIRS are compatible — which the old per-direction
    vocabulary cannot express in the first place."""
    grouped = _movements_by_direction(junction)
    phases: list[Phase] = []
    plan: list[SignalPlanEntry] = []
    for direction in sorted(grouped, key=lambda d: d.value):
        green_ms = max(
            (
                general_phase.min_green_ms
                for general_phase in junction.phases
                if any(m.id in general_phase.movement_ids for m in grouped[direction])
            ),
            default=settings.min_green_ms,
        )
        phase_id = f"{junction.id}-P{direction.value}"
        phases.append(
            Phase(
                id=phase_id,
                movements=[direction],
                min_green_ms=settings.min_green_ms,
                amber_ms=settings.amber_ms,
                all_red_ms=settings.all_red_ms,
                ped_clearance_ms=settings.ped_clearance_ms,
            )
        )
        plan.append(SignalPlanEntry(phase_id=phase_id, green_ms=green_ms))
    return phases, plan


def _adapt_approaches(junction: Junction) -> list[Approach]:
    return [
        Approach(
            id=approach.id,
            intersection_id=junction.id,
            direction=_direction_of_bearing(approach.bearing_deg),
            lanes=approach.lanes,
        )
        for approach in junction.approaches
    ]


def adapt_intersection(junction: Junction, settings: Settings) -> Intersection:
    if junction.kind != IntersectionKind.SIGNALISED:
        raise LegacyAdapterError(
            f"junction {junction.id} is {junction.kind}; the old model only supports SIGNALISED"
        )
    phases, plan = _adapt_phases(junction, settings)
    return Intersection(
        id=junction.id,
        x=junction.x,
        y=junction.y,
        approaches=_adapt_approaches(junction),
        phases=phases,
        conflict_matrix=_adapt_conflict_matrix(junction, settings),
        default_plan=plan,
        cycle_offset_ms=0,
    )


def adapt_segment(segment: GeneralRoadSegment) -> RoadSegment:
    return RoadSegment(
        id=segment.id,
        from_node=segment.from_node,
        to_node=segment.to_node,
        length_m=segment.length_m,
        lanes=segment.lanes,
        free_flow_speed_mps=segment.free_flow_speed_mps,
        capacity_vph=segment.capacity_vph,
    )


def adapt_hospital(hospital: Hospital) -> Hospital:
    return hospital.model_copy()


def adapt_to_legacy_city(general: GeneralCity, settings: Settings) -> City:
    return City(
        intersections=[adapt_intersection(j, settings) for j in general.intersections],
        roads=[adapt_segment(s) for s in general.roads],
        hospitals=[adapt_hospital(h) for h in general.hospitals],
        ambulance_entry_node=general.ambulance_entry_node,
    )
