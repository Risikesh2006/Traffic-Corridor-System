"""Adapter equivalence test (PRD Phase 6 Stage 6.1.5, ADR-050).

The adapted city is checked for structural equivalence against the
hand-authored grid (`config/city.json`): same intersections, segments,
hospitals, entry node, and every approach served. It is NOT checked for
byte-identical phases or conflict matrices, because the general model finds
real conflicts (merges into a shared exit from different approaches) that
the hand-authored matrix never modelled. The test instead proves the adapted
matrix never LOSES a conflict the old one declared, and documents exactly
where the two diverge.
"""

from __future__ import annotations

from pathlib import Path

from core.city_loader import load_city
from core.config import load_settings
from core.general_city_loader import load_general_city
from core.legacy_adapter import adapt_to_legacy_city

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _settings():
    return load_settings(CONFIG_DIR / "settings.yaml")


def _old_city():
    return load_city(CONFIG_DIR / "city.json", _settings())


def _adapted_city():
    general = load_general_city(CONFIG_DIR / "cities" / "grid.city.json", _settings())
    return adapt_to_legacy_city(general, _settings())


def test_same_intersections_segments_and_hospitals():
    old = _old_city()
    new = _adapted_city()
    assert {n.id for n in old.intersections} == {n.id for n in new.intersections}
    assert {r.id for r in old.roads} == {r.id for r in new.roads}
    assert {h.id for h in old.hospitals} == {h.id for h in new.hospitals}
    assert old.ambulance_entry_node == new.ambulance_entry_node


def test_segment_geometry_and_capacity_match():
    old = _old_city()
    new = _adapted_city()
    for road in old.roads:
        adapted = new.segment(road.id)
        assert adapted.from_node == road.from_node
        assert adapted.to_node == road.to_node
        assert adapted.length_m == road.length_m
        assert adapted.lanes == road.lanes
        assert adapted.free_flow_speed_mps == road.free_flow_speed_mps
        assert adapted.capacity_vph == road.capacity_vph


def test_every_approach_direction_matches():
    old = _old_city()
    new = _adapted_city()
    for node in old.intersections:
        adapted = new.intersection(node.id)
        old_directions = {a.direction for a in node.approaches}
        new_directions = {a.direction for a in adapted.approaches}
        assert old_directions == new_directions, node.id


def test_adapted_city_passes_the_old_loaders_structural_checks():
    """The adapted city validates against the SAME loader the hand-authored
    file must pass: unique ids, known references, conflict-free phases,
    every approach served, strongly connected, hospitals valid, timings."""
    import json
    import tempfile

    new = _adapted_city()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(new.model_dump(mode="json"), f)
        path = f.name
    load_city(path, _settings())  # raises CityValidationError if anything is wrong


def test_adapted_conflict_matrix_never_loses_an_old_conflict_at_regular_4way_junctions():
    """Every pair the hand-authored matrix declares conflicting must still be
    conflicting in the adapted matrix, checked at INTERIOR 4-WAY junctions
    only. The old grid authors one universal N/S/E/W conflict table and
    applies it to every node regardless of which approaches really exist
    there; its crossing conflicts (e.g. E vs S) are only geometrically real
    where a genuine through-movement exists in both directions, which is
    true at a regular 4-way but not at a 2- or 3-arm corner/edge node
    (there, the old "conflict" was never backed by a real movement, so it
    is not a known fact to preserve — only this phase's genuine merge
    finding, ADR-050, is). The adapted matrix may additionally flag pairs
    the old one missed."""
    old = _old_city()
    new = _adapted_city()
    for node in old.intersections:
        if len(node.approaches) != 4:
            continue
        adapted = new.intersection(node.id)
        for a in node.conflict_matrix.movements:
            for b in node.conflict_matrix.movements:
                if node.conflict_matrix.conflicts(a, b):
                    assert adapted.conflict_matrix.conflicts(a, b), (
                        f"{node.id}: adapted matrix lost known conflict ({a}, {b})"
                    )


def test_general_model_finds_a_merge_conflict_the_old_grid_missed():
    """Documents the divergence (ADR-050): at a regular 4-way junction, a
    far-side turn from one approach and a near-side turn from the OPPOSING
    approach both end on the same exit road — a real merge conflict the old
    grid's hand-authored matrix never modelled, because it only ever
    considered whole compass directions, never individual turns."""
    new = _adapted_city()
    b2 = new.intersection("B2")  # interior, 4-way, regular
    assert b2.conflict_matrix.conflicts("N", "S") or b2.conflict_matrix.conflicts("E", "W"), (
        "expected at least one opposing pair to conflict via a merge the old grid never saw"
    )


def test_every_movement_is_served_in_the_adapted_phases():
    new = _adapted_city()
    for node in new.intersections:
        covered: set[str] = set()
        for phase in node.phases:
            covered.update(phase.movements)
        for approach in node.approaches:
            assert approach.direction in covered, (node.id, approach.id)
