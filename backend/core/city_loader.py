"""Loads and validates `config/city.json` into a `City` domain model (PRD §9, §10.1).

Fails loudly and specifically: every structural problem in a city file is a
distinct, named error so a bad config can never silently produce an unsafe
or nonsensical network.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.config import Settings
from core.models import City, Direction, Hospital, Intersection, RoadSegment


class CityValidationError(Exception):
    """Raised for any structural or semantic problem in a city config."""


def load_city(path: str | Path, settings: Settings) -> City:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    try:
        intersections = [Intersection(**node) for node in raw["intersections"]]
        roads = [RoadSegment(**road) for road in raw["roads"]]
        hospitals = [Hospital(**h) for h in raw["hospitals"]]
    except (ValidationError, KeyError, TypeError) as exc:
        raise CityValidationError(f"malformed city config: {exc}") from exc

    ambulance_entry_node = raw.get("ambulance_entry_node")
    if not ambulance_entry_node:
        raise CityValidationError("city config missing 'ambulance_entry_node'")

    city = City(
        intersections=intersections,
        roads=roads,
        hospitals=hospitals,
        ambulance_entry_node=ambulance_entry_node,
    )

    _validate_unique_ids(city)
    _validate_segments_reference_known_nodes(city)
    _validate_conflict_matrices(raw["intersections"])
    _validate_phases_conflict_free(city)
    _validate_every_approach_served_by_a_phase(city)
    _validate_hospitals_on_known_nodes(city)
    _validate_strongly_connected(city)
    _validate_timings_against_settings(city, settings)
    _validate_ambulance_entry_known(city)

    return city


def _validate_unique_ids(city: City) -> None:
    node_ids = [n.id for n in city.intersections]
    if len(node_ids) != len(set(node_ids)):
        dupes = {x for x in node_ids if node_ids.count(x) > 1}
        raise CityValidationError(f"duplicate intersection IDs: {sorted(dupes)}")

    road_ids = [r.id for r in city.roads]
    if len(road_ids) != len(set(road_ids)):
        dupes = {x for x in road_ids if road_ids.count(x) > 1}
        raise CityValidationError(f"duplicate road segment IDs: {sorted(dupes)}")

    hospital_ids = [h.id for h in city.hospitals]
    if len(hospital_ids) != len(set(hospital_ids)):
        dupes = {x for x in hospital_ids if hospital_ids.count(x) > 1}
        raise CityValidationError(f"duplicate hospital IDs: {sorted(dupes)}")


def _validate_segments_reference_known_nodes(city: City) -> None:
    node_ids = {n.id for n in city.intersections}
    for road in city.roads:
        if road.from_node not in node_ids:
            raise CityValidationError(
                f"road {road.id} references unknown from_node '{road.from_node}'"
            )
        if road.to_node not in node_ids:
            raise CityValidationError(
                f"road {road.id} references unknown to_node '{road.to_node}'"
            )


def _validate_conflict_matrices(raw_intersections: list[dict[str, Any]]) -> None:
    """Checks the *authored* conflict pairs are explicitly symmetric and
    contain no self-conflicts. (`ConflictMatrix.conflicts()` itself behaves
    symmetrically regardless, but we require the config to spell both
    directions out for human auditability.)
    """
    for node in raw_intersections:
        node_id = node["id"]
        pairs = [tuple(p) for p in node["conflict_matrix"]["conflict_pairs"]]
        pair_set = set(pairs)
        for a, b in pairs:
            if a == b:
                raise CityValidationError(
                    f"intersection {node_id}: self-conflict ({a}, {a}) in conflict matrix"
                )
            if (b, a) not in pair_set:
                raise CityValidationError(
                    f"intersection {node_id}: conflict matrix not symmetric — "
                    f"({a}, {b}) present without ({b}, {a})"
                )


def _validate_phases_conflict_free(city: City) -> None:
    for node in city.intersections:
        for phase in node.phases:
            if not node.conflict_matrix.is_compatible(set(phase.movements)):
                raise CityValidationError(
                    f"intersection {node.id}: phase {phase.id} contains mutually "
                    f"conflicting movements {phase.movements}"
                )


def _validate_every_approach_served_by_a_phase(city: City) -> None:
    for node in city.intersections:
        covered: set[Direction] = set()
        for phase in node.phases:
            covered.update(phase.movements)
        for approach in node.approaches:
            if approach.direction not in covered:
                raise CityValidationError(
                    f"intersection {node.id}: approach {approach.id} "
                    f"(direction {approach.direction}) is not served by any phase"
                )


def _validate_hospitals_on_known_nodes(city: City) -> None:
    node_ids = {n.id for n in city.intersections}
    for hospital in city.hospitals:
        if hospital.node_id not in node_ids:
            raise CityValidationError(
                f"hospital {hospital.id} references unknown node '{hospital.node_id}'"
            )


def _validate_strongly_connected(city: City) -> None:
    node_ids = {n.id for n in city.intersections}
    if not node_ids:
        raise CityValidationError("city has no intersections")

    forward: dict[str, list[str]] = {n: [] for n in node_ids}
    backward: dict[str, list[str]] = {n: [] for n in node_ids}
    for road in city.roads:
        forward[road.from_node].append(road.to_node)
        backward[road.to_node].append(road.from_node)

    start = next(iter(node_ids))
    reachable_forward = _bfs(start, forward)
    reachable_backward = _bfs(start, backward)

    if reachable_forward != node_ids:
        unreachable = node_ids - reachable_forward
        raise CityValidationError(
            f"graph not strongly connected: unreachable from {start} (forward): "
            f"{sorted(unreachable)}"
        )
    if reachable_backward != node_ids:
        unreachable = node_ids - reachable_backward
        raise CityValidationError(
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


def _validate_timings_against_settings(city: City, settings: Settings) -> None:
    for node in city.intersections:
        for phase in node.phases:
            if phase.min_green_ms < settings.min_green_ms:
                raise CityValidationError(
                    f"intersection {node.id}: phase {phase.id} min_green_ms "
                    f"{phase.min_green_ms} is shorter than settings.min_green_ms "
                    f"{settings.min_green_ms}"
                )
            if phase.amber_ms < settings.amber_ms:
                raise CityValidationError(
                    f"intersection {node.id}: phase {phase.id} amber_ms "
                    f"{phase.amber_ms} is shorter than settings.amber_ms {settings.amber_ms}"
                )
            if phase.all_red_ms < settings.all_red_ms:
                raise CityValidationError(
                    f"intersection {node.id}: phase {phase.id} all_red_ms "
                    f"{phase.all_red_ms} is shorter than settings.all_red_ms "
                    f"{settings.all_red_ms}"
                )
        for entry in node.default_plan:
            matching_phase = next((p for p in node.phases if p.id == entry.phase_id), None)
            if matching_phase is None:
                raise CityValidationError(
                    f"intersection {node.id}: default_plan references unknown "
                    f"phase '{entry.phase_id}'"
                )
            if entry.green_ms < matching_phase.min_green_ms:
                raise CityValidationError(
                    f"intersection {node.id}: default_plan green_ms {entry.green_ms} "
                    f"for phase {entry.phase_id} is shorter than its min_green_ms "
                    f"{matching_phase.min_green_ms}"
                )


def _validate_ambulance_entry_known(city: City) -> None:
    node_ids = {n.id for n in city.intersections}
    if city.ambulance_entry_node not in node_ids:
        raise CityValidationError(
            f"ambulance_entry_node '{city.ambulance_entry_node}' is not a known intersection"
        )


__all__ = ["CityValidationError", "load_city"]
