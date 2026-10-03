"""Automatic signal phase generation (PRD Phase 6 Stage 6.1.3, ADR-048).

Plans are generated from geometry and lane counts, because real signal timings
are not public. Two strategies:

- PAIRED: a regular four-way junction whose opposing arms are aligned within a
  bearing tolerance. Each opposing pair shares a phase for its through and
  near-side movements. Far-side movements of the pair get their own phases.
- SPLIT: one phase per approach, with all of its non-U-turn movements together.
  Common at Indian junctions, and conflict-free by construction except for the
  merges that the conflict matrix still checks.

PAIRED is chosen only when it passes the same checks as SPLIT would, so a
regular junction never gets an invalid plan. Every phase must be compatible,
and every non-U-turn movement must be served. Otherwise generation fails loudly.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.clock import SimTimeMs
from core.config import Settings
from core.conflicts import ConflictRules
from core.junctions import Junction, JunctionConflictMatrix, JunctionPhase, Movement
from core.models import IntersectionKind, TurnType


class PhasingError(ValueError):
    pass


def _angle_gap(a: float, b: float) -> float:
    return (b - a) % 360.0


def _opposing_pairs(junction: Junction, tolerance_deg: float) -> list[tuple[str, str]] | None:
    """Pairs of approaches that face each other, or None if the junction is irregular."""
    if len(junction.approaches) != 4:
        return None
    arms = sorted(junction.approaches, key=lambda a: (a.bearing_deg, a.id))
    pairs: list[tuple[str, str]] = []
    used: set[str] = set()
    for first in arms:
        if first.id in used:
            continue
        match = None
        for second in arms:
            if second.id == first.id or second.id in used:
                continue
            if abs(_angle_gap(first.bearing_deg, second.bearing_deg) - 180.0) <= tolerance_deg:
                match = second
                break
        if match is None:
            return None
        pairs.append((first.id, match.id))
        used.update((first.id, match.id))
    return pairs


def _movements_by_approach(junction: Junction) -> dict[str, list[Movement]]:
    grouped: dict[str, list[Movement]] = {a.id: [] for a in junction.approaches}
    for movement in junction.movements:
        if movement.turn_type == TurnType.U_TURN:
            continue
        grouped[movement.approach_id].append(movement)
    return grouped


def _split_plan(junction: Junction) -> list[tuple[str, ...]]:
    grouped = _movements_by_approach(junction)
    return [
        tuple(sorted(m.id for m in grouped[a.id]))
        for a in sorted(junction.approaches, key=lambda a: a.id)
        if grouped[a.id]
    ]


def _paired_plan(junction: Junction, settings: Settings) -> list[tuple[str, ...]] | None:
    pairs = _opposing_pairs(junction, settings.regular_bearing_tolerance_deg)
    if pairs is None:
        return None
    grouped = _movements_by_approach(junction)
    phases: list[tuple[str, ...]] = []
    for first, second in pairs:
        through_and_near = [
            m.id
            for approach in (first, second)
            for m in grouped[approach]
            if m.turn_type in (TurnType.THROUGH, TurnType.NEAR_SIDE)
        ]
        if through_and_near:
            phases.append(tuple(sorted(through_and_near)))
        for approach in (first, second):
            far = [m.id for m in grouped[approach] if m.turn_type == TurnType.FAR_SIDE]
            if far:
                phases.append(tuple(sorted(far)))
    return phases


def _all_compatible(phases: Iterable[tuple[str, ...]], matrix: JunctionConflictMatrix) -> bool:
    return all(matrix.is_compatible(p) for p in phases)


def _served(phases: list[tuple[str, ...]], junction: Junction) -> set[str]:
    return {mid for phase in phases for mid in phase}


def select_plan(junction: Junction, settings: Settings) -> tuple[str, list[tuple[str, ...]]]:
    """Return ('PAIRED' | 'SPLIT', movement groups) for a signalised junction."""
    rules = ConflictRules()
    matrix = junction.conflict_matrix(rules)
    expected = {m.id for m in junction.movements if m.turn_type != TurnType.U_TURN}
    paired = _paired_plan(junction, settings)
    if (
        paired is not None
        and _all_compatible(paired, matrix)
        and _served(paired, junction) == expected
    ):
        return "PAIRED", paired
    split = _split_plan(junction)
    if not _all_compatible(split, matrix):
        raise PhasingError(f"junction {junction.id}: split phases are not conflict-free")
    if _served(split, junction) != expected:
        raise PhasingError(f"junction {junction.id}: some movements are not served by any phase")
    return "SPLIT", split


def generate_phases(junction: Junction, settings: Settings) -> tuple[JunctionPhase, ...]:
    if junction.kind != IntersectionKind.SIGNALISED:
        raise PhasingError(f"junction {junction.id} is {junction.kind}, not SIGNALISED")
    _strategy, groups = select_plan(junction, settings)
    if not groups:
        raise PhasingError(f"junction {junction.id} has no movements to serve")
    lanes = {a.id: a.lanes for a in junction.approaches}
    approach_of = {m.id: m.approach_id for m in junction.movements}
    ordered = sorted(groups)
    amber = settings.amber_ms
    all_red = settings.all_red_ms
    lost = len(ordered) * (amber + all_red)
    cycle = min(max(settings.cycle_target_ms, settings.cycle_min_ms), settings.cycle_max_ms)
    green_total = cycle - lost
    if green_total <= 0:
        raise PhasingError(f"junction {junction.id}: cycle too short for {len(ordered)} phases")
    weights = [sum(lanes[a] for a in {approach_of[m] for m in group}) for group in ordered]
    total_weight = sum(weights)
    phases: list[JunctionPhase] = []
    for index, (group, weight) in enumerate(zip(ordered, weights, strict=True)):
        green = round(green_total * weight / total_weight)
        green = min(max(green, settings.min_green_ms), settings.phase_green_max_ms)
        phases.append(
            JunctionPhase(
                id=f"{junction.id}-P{index + 1}",
                intersection_id=junction.id,
                movement_ids=group,
                min_green_ms=green,
                amber_ms=amber,
                all_red_ms=all_red,
                ped_clearance_ms=settings.ped_clearance_ms,
            )
        )
    result = tuple(phases)
    total = cycle_ms(result)
    if not settings.cycle_min_ms <= total <= settings.cycle_max_ms:
        raise PhasingError(
            f"junction {junction.id}: cycle {total} ms is outside the configured bounds"
        )
    return result


def cycle_ms(phases: tuple[JunctionPhase, ...]) -> SimTimeMs:
    return sum(p.min_green_ms + p.amber_ms + p.all_red_ms for p in phases)
