from pathlib import Path

from core.city_loader import load_city
from core.config import load_settings
from core.models import Direction
from sim.topology import (
    TURN_MAP,
    compute_approach_directions,
    compute_exits,
    is_border_intersection,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _city():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    return load_city(CONFIG_DIR / "city.json", settings)


def test_approach_direction_matches_known_segment():
    city = _city()
    directions = compute_approach_directions(city)
    # C4->D4 feeds D4 from the west (C is west of D).
    assert directions["C4->D4"] == Direction.W
    # A1->A2 feeds A2 from the north (row 1 is north of row 2).
    assert directions["A1->A2"] == Direction.N


def test_exits_round_trip_with_approach_directions():
    city = _city()
    exits = compute_exits(city)
    directions = compute_approach_directions(city)
    # Every segment should appear as an exit of its from_node in the
    # direction opposite to the approach direction it feeds downstream.
    for road in city.roads:
        departure_dir = next(d for d, sid in exits[road.from_node].items() if sid == road.id)
        arrival_dir = directions[road.id]
        assert departure_dir != arrival_dir  # a segment never arrives from the way it left


def test_border_intersections_detected():
    city = _city()
    assert is_border_intersection(city, "A1")
    assert is_border_intersection(city, "F6")
    assert not is_border_intersection(city, "B2")


def test_turn_map_is_self_consistent_bijection():
    for direction, turns in TURN_MAP.items():
        targets = set(turns.values())
        assert len(targets) == 3  # through/left/right all distinct
        assert direction not in targets  # never loops back the way it came
