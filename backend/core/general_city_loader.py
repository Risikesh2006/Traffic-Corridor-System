"""Loads and validates a general-format city file (PRD Phase 6 Stage 6.1.4).

Mirrors `core.city_loader`'s philosophy for the new model: every structural
problem is a distinct, named error, so a bad city file can never silently
produce an unsafe or nonsensical network. Unlike the old format, conflict
matrices and phase coverage are not authored by hand — they are derived from
geometry (ADR-046) and generated (ADR-048) — so this loader re-derives and
checks them rather than trusting what is stored.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from pydantic import ValidationError

from core.config import Settings
from core.conflicts import ConflictRules
from core.general_city import GeneralCity, GeneralRoadSegment
from core.junctions import Junction
from core.models import Hospital


class GeneralCityValidationError(Exception):
    """Raised for any structural or semantic problem in a general city file."""


def load_general_city(path: str | Path, settings: Settings) -> GeneralCity:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    try:
        junctions = tuple(Junction(**j) for j in raw["junctions"])
        segments = tuple(GeneralRoadSegment(**s) for s in raw["segments"])
        hospitals = tuple(Hospital(**h) for h in raw["hospitals"])
    except (ValidationError, KeyError, TypeError) as exc:
        raise GeneralCityValidationError(f"malformed city file: {exc}") from exc

    city = GeneralCity(
        junctions=junctions,
        segments=segments,
        hospitals=hospitals,
        ambulance_entry_node=raw["ambulance_entry_node"],
    )

    _validate_unique_ids(city)
    _validate_segments_reference_known_junctions(city)
    _validate_junction_internal_references(city)
    _validate_conflict_matrices_symmetric_and_irreflexive(city)
    _validate_phases_conflict_free_and_served(city)
    _validate_hospitals_on_known_nodes(city)
    _validate_strongly_connected(city)
    _validate_timings_against_settings(city, settings)
    _validate_ambulance_entry_known(city)
    return city


def _validate_unique_ids(city: GeneralCity) -> None:
    junction_ids = [j.id for j in city.junctions]
    if len(junction_ids) != len(set(junction_ids)):
        dupes = {x for x in junction_ids if junction_ids.count(x) > 1}
        raise GeneralCityValidationError(f"duplicate junction IDs: {sorted(dupes)}")

    segment_ids = [s.id for s in city.segments]
    if len(segment_ids) != len(set(segment_ids)):
        dupes = {x for x in segment_ids if segment_ids.count(x) > 1}
        raise GeneralCityValidationError(f"duplicate segment IDs: {sorted(dupes)}")

    hospital_ids = [h.id for h in city.hospitals]
    if len(hospital_ids) != len(set(hospital_ids)):
        dupes = {x for x in hospital_ids if hospital_ids.count(x) > 1}
        raise GeneralCityValidationError(f"duplicate hospital IDs: {sorted(dupes)}")

    for junction in city.junctions:
        _validate_unique(junction.id, "approach", [a.id for a in junction.approaches])
        _validate_unique(junction.id, "exit", [e.id for e in junction.exits])
        _validate_unique(junction.id, "movement", [m.id for m in junction.movements])
        _validate_unique(junction.id, "phase", [p.id for p in junction.phases])


def _validate_unique(junction_id: str, kind: str, ids: list[str]) -> None:
    if len(ids) != len(set(ids)):
        dupes = {x for x in ids if ids.count(x) > 1}
        raise GeneralCityValidationError(
            f"junction {junction_id}: duplicate {kind} IDs: {sorted(dupes)}"
        )


def _validate_segments_reference_known_junctions(city: GeneralCity) -> None:
    junction_ids = {j.id for j in city.junctions}
    for segment in city.segments:
        if segment.from_node not in junction_ids:
            raise GeneralCityValidationError(
                f"segment {segment.id} references unknown from_node '{segment.from_node}'"
            )
        if segment.to_node not in junction_ids:
            raise GeneralCityValidationError(
                f"segment {segment.id} references unknown to_node '{segment.to_node}'"
            )


def _validate_junction_internal_references(city: GeneralCity) -> None:
    segment_ids = {s.id for s in city.segments}
    for junction in city.junctions:
        approach_ids = {a.id for a in junction.approaches}
        exit_ids = {e.id for e in junction.exits}
        for approach in junction.approaches:
            if approach.intersection_id != junction.id:
                raise GeneralCityValidationError(
                    f"approach {approach.id} belongs to junction {approach.intersection_id}, "
                    f"not {junction.id}"
                )
            if approach.segment_id not in segment_ids:
                raise GeneralCityValidationError(
                    f"approach {approach.id} references unknown segment '{approach.segment_id}'"
                )
        for exit_ in junction.exits:
            if exit_.intersection_id != junction.id:
                raise GeneralCityValidationError(
                    f"exit {exit_.id} belongs to junction {exit_.intersection_id}, "
                    f"not {junction.id}"
                )
            if exit_.segment_id not in segment_ids:
                raise GeneralCityValidationError(
                    f"exit {exit_.id} references unknown segment '{exit_.segment_id}'"
                )
        for movement in junction.movements:
            if movement.approach_id not in approach_ids:
                raise GeneralCityValidationError(
                    f"movement {movement.id} references unknown approach '{movement.approach_id}' "
                    f"at junction {junction.id}"
                )
            if movement.exit_id not in exit_ids:
                raise GeneralCityValidationError(
                    f"movement {movement.id} references unknown exit '{movement.exit_id}' "
                    f"at junction {junction.id}"
                )
        movement_ids = {m.id for m in junction.movements}
        for phase in junction.phases:
            for movement_id in phase.movement_ids:
                if movement_id not in movement_ids:
                    raise GeneralCityValidationError(
                        f"junction {junction.id}: phase {phase.id} references unknown "
                        f"movement '{movement_id}'"
                    )


def _validate_conflict_matrices_symmetric_and_irreflexive(city: GeneralCity) -> None:
    rules = ConflictRules()
    for junction in city.junctions:
        matrix = junction.conflict_matrix(rules)
        for a in matrix.movement_ids:
            if matrix.conflicts(a, a):
                raise GeneralCityValidationError(
                    f"junction {junction.id}: movement {a} conflicts with itself"
                )
            for b in matrix.movement_ids:
                if matrix.conflicts(a, b) != matrix.conflicts(b, a):
                    raise GeneralCityValidationError(
                        f"junction {junction.id}: conflict matrix is not symmetric for ({a}, {b})"
                    )


def _validate_phases_conflict_free_and_served(city: GeneralCity) -> None:
    rules = ConflictRules()
    for junction in city.junctions:
        if not junction.phases:
            continue
        matrix = junction.conflict_matrix(rules)
        covered: set[str] = set()
        for phase in junction.phases:
            if not matrix.is_compatible(phase.movement_ids):
                raise GeneralCityValidationError(
                    f"junction {junction.id}: phase {phase.id} contains mutually "
                    f"conflicting movements {phase.movement_ids}"
                )
            covered.update(phase.movement_ids)
        expected = {m.id for m in junction.movements if m.turn_type.value != "U_TURN"}
        unserved = expected - covered
        if unserved:
            raise GeneralCityValidationError(
                f"junction {junction.id}: movements not served by any phase: {sorted(unserved)}"
            )


def _validate_hospitals_on_known_nodes(city: GeneralCity) -> None:
    junction_ids = {j.id for j in city.junctions}
    for hospital in city.hospitals:
        if hospital.node_id not in junction_ids:
            raise GeneralCityValidationError(
                f"hospital {hospital.id} references unknown node '{hospital.node_id}'"
            )


def _validate_strongly_connected(city: GeneralCity) -> None:
    junction_ids = {j.id for j in city.junctions}
    if not junction_ids:
        raise GeneralCityValidationError("city has no junctions")

    forward: dict[str, list[str]] = {j: [] for j in junction_ids}
    backward: dict[str, list[str]] = {j: [] for j in junction_ids}
    for segment in city.segments:
        forward[segment.from_node].append(segment.to_node)
        backward[segment.to_node].append(segment.from_node)

    start = next(iter(junction_ids))
    reachable_forward = _bfs(start, forward)
    reachable_backward = _bfs(start, backward)

    if reachable_forward != junction_ids:
        unreachable = junction_ids - reachable_forward
        raise GeneralCityValidationError(
            f"graph not strongly connected: unreachable from {start} (forward): "
            f"{sorted(unreachable)}"
        )
    if reachable_backward != junction_ids:
        unreachable = junction_ids - reachable_backward
        raise GeneralCityValidationError(
            f"graph not strongly connected: cannot reach {start} from (backward): "
            f"{sorted(unreachable)}"
        )


def _bfs(start: str, adjacency: dict[str, list[str]]) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def _validate_timings_against_settings(city: GeneralCity, settings: Settings) -> None:
    for junction in city.junctions:
        for phase in junction.phases:
            if phase.min_green_ms < settings.min_green_ms:
                raise GeneralCityValidationError(
                    f"junction {junction.id}: phase {phase.id} min_green_ms {phase.min_green_ms} "
                    f"is shorter than settings.min_green_ms {settings.min_green_ms}"
                )
            if phase.amber_ms < settings.amber_ms:
                raise GeneralCityValidationError(
                    f"junction {junction.id}: phase {phase.id} amber_ms {phase.amber_ms} "
                    f"is shorter than settings.amber_ms {settings.amber_ms}"
                )
            if phase.all_red_ms < settings.all_red_ms:
                raise GeneralCityValidationError(
                    f"junction {junction.id}: phase {phase.id} all_red_ms {phase.all_red_ms} "
                    f"is shorter than settings.all_red_ms {settings.all_red_ms}"
                )


def _validate_ambulance_entry_known(city: GeneralCity) -> None:
    junction_ids = {j.id for j in city.junctions}
    if city.ambulance_entry_node not in junction_ids:
        raise GeneralCityValidationError(
            f"ambulance_entry_node '{city.ambulance_entry_node}' is not a known junction"
        )


__all__ = ["GeneralCityValidationError", "load_general_city"]
