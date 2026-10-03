from pathlib import Path

import pytest

from core.city_loader import load_city
from core.config import load_settings
from tracking.map_match import MapMatcher

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


@pytest.fixture(scope="module")
def matcher() -> MapMatcher:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    return MapMatcher(city, radius_m=settings.map_match_radius_m)


def _xy(node: str) -> tuple[float, float]:
    # Matches the grid convention: column A..F -> x = index*300, row 1..6 -> y = (row-1)*300.
    col = ord(node[0]) - ord("A")
    row = int(node[1:]) - 1
    return col * 300.0, row * 300.0


def test_matches_correct_segment_on_straight_road(matcher: MapMatcher) -> None:
    # Midpoint of A2->B2 (eastbound), heading east (0 deg).
    ax, ay = _xy("A2")
    bx, by = _xy("B2")
    x, y = (ax + bx) / 2, (ay + by) / 2
    result = matcher.match(x, y, heading_deg=0.0)
    assert result is not None
    assert result.segment_id == "A2->B2"
    assert result.offset_m == pytest.approx(150.0, abs=0.5)
    assert result.quality > 0.9


def test_heading_disambiguates_opposite_carriageways(matcher: MapMatcher) -> None:
    # Same physical location is covered by both A2->B2 (east) and B2->A2 (west).
    ax, ay = _xy("A2")
    bx, by = _xy("B2")
    x, y = (ax + bx) / 2, (ay + by) / 2
    east = matcher.match(x, y, heading_deg=0.0)
    west = matcher.match(x, y, heading_deg=180.0)
    assert east.segment_id == "A2->B2"
    assert west.segment_id == "B2->A2"


def test_route_bonus_breaks_tie_toward_route(matcher: MapMatcher) -> None:
    ax, ay = _xy("C4")
    bx, by = _xy("D4")
    x, y = (ax + bx) / 2, (ay + by) / 2
    # Heading is ambiguous enough that the route should decide between the
    # intersecting cross-street, not distance.
    on_route = matcher.match(x, y, heading_deg=0.0, route_segments=frozenset({"C4->D4"}))
    assert on_route is not None
    assert on_route.segment_id == "C4->D4"


def test_noisy_fix_stays_matched_to_correct_segment(matcher: MapMatcher) -> None:
    import random

    rng = random.Random(4417)
    ax, ay = _xy("B3")
    bx, by = _xy("B4")
    correct = 0
    trials = 200
    for _ in range(trials):
        t = rng.uniform(0.2, 0.8)
        x = ax + (bx - ax) * t + rng.gauss(0, 5.0)
        y = ay + (by - ay) * t + rng.gauss(0, 5.0)
        result = matcher.match(x, y, heading_deg=90.0)
        if result is not None and result.segment_id == "B3->B4":
            correct += 1
    assert correct / trials > 0.95


def test_no_match_far_from_any_road(matcher: MapMatcher) -> None:
    assert matcher.match(10_000.0, 10_000.0, heading_deg=0.0) is None


def test_continuity_keeps_match_on_previous_segment_or_its_successors(matcher: MapMatcher) -> None:
    # Fix sitting right on the node B2: several segments meet here. Continuity
    # rewards the previous segment and anything leaving B2 from it, so the
    # result must stay within that set rather than jumping to an unrelated road.
    x, y = _xy("B2")
    biased = matcher.match(x + 2.0, y + 2.0, heading_deg=0.0, previous_segment="A2->B2")
    assert biased is not None
    allowed = {"A2->B2"} | {r.id for r in matcher.city.outgoing("B2")}
    assert biased.segment_id in allowed
