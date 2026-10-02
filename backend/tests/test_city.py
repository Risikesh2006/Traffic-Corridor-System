import copy
import json
from pathlib import Path

import pytest

from core.city_loader import CityValidationError, load_city
from core.config import Settings, load_settings

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return load_settings(CONFIG_DIR / "settings.yaml")


@pytest.fixture(scope="module")
def raw_city() -> dict:
    return json.loads((CONFIG_DIR / "city.json").read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "city.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_generated_city_passes_validation(settings: Settings) -> None:
    city = load_city(CONFIG_DIR / "city.json", settings)
    assert city is not None


def test_counts_are_correct(settings: Settings) -> None:
    city = load_city(CONFIG_DIR / "city.json", settings)
    assert len(city.intersections) == 36
    assert len(city.roads) == 120


def test_corner_intersections_have_two_approaches(settings: Settings) -> None:
    city = load_city(CONFIG_DIR / "city.json", settings)
    for corner in ("A1", "A6", "F1", "F6"):
        assert len(city.intersection(corner).approaches) == 2


def test_edge_intersections_have_three_approaches(settings: Settings) -> None:
    city = load_city(CONFIG_DIR / "city.json", settings)
    assert len(city.intersection("A3").approaches) == 3  # west edge
    assert len(city.intersection("C1").approaches) == 3  # north edge


def test_interior_intersections_have_four_approaches(settings: Settings) -> None:
    city = load_city(CONFIG_DIR / "city.json", settings)
    assert len(city.intersection("B2").approaches) == 4


def test_graph_is_strongly_connected(settings: Settings) -> None:
    city = load_city(CONFIG_DIR / "city.json", settings)
    assert city.neighbors("A1")  # sanity: has outgoing edges
    # Validated fully inside load_city(); re-run confirms no exception.


def test_hospitals_at_expected_nodes(settings: Settings) -> None:
    city = load_city(CONFIG_DIR / "city.json", settings)
    node_ids = {h.node_id for h in city.hospitals}
    assert node_ids == {"F1", "F6"}


def test_ambulance_entry_is_a6(settings: Settings) -> None:
    city = load_city(CONFIG_DIR / "city.json", settings)
    assert city.ambulance_entry_node == "A6"


# --- Deliberately broken fixtures ---


def test_duplicate_intersection_id_rejected(
    tmp_path: Path, raw_city: dict, settings: Settings
) -> None:
    broken = copy.deepcopy(raw_city)
    broken["intersections"].append(broken["intersections"][0])
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="duplicate intersection"):
        load_city(path, settings)


def test_segment_referencing_unknown_node_rejected(
    tmp_path: Path, raw_city: dict, settings: Settings
) -> None:
    broken = copy.deepcopy(raw_city)
    broken["roads"][0]["to_node"] = "Z9"
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="unknown to_node"):
        load_city(path, settings)


def test_asymmetric_conflict_matrix_rejected(
    tmp_path: Path, raw_city: dict, settings: Settings
) -> None:
    broken = copy.deepcopy(raw_city)
    broken["intersections"][0]["conflict_matrix"]["conflict_pairs"] = [["N", "E"]]
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="not symmetric"):
        load_city(path, settings)


def test_self_conflict_rejected(tmp_path: Path, raw_city: dict, settings: Settings) -> None:
    broken = copy.deepcopy(raw_city)
    broken["intersections"][0]["conflict_matrix"]["conflict_pairs"] = [["N", "N"]]
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="self-conflict"):
        load_city(path, settings)


def test_phase_with_conflicting_movements_rejected(
    tmp_path: Path, raw_city: dict, settings: Settings
) -> None:
    broken = copy.deepcopy(raw_city)
    broken["intersections"][0]["phases"][0]["movements"] = ["N", "E"]
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="conflicting movements"):
        load_city(path, settings)


def test_approach_not_served_by_any_phase_rejected(
    tmp_path: Path, raw_city: dict, settings: Settings
) -> None:
    broken = copy.deepcopy(raw_city)
    # B2 is interior (has all 4 approaches); strip phases down to NS only.
    for node in broken["intersections"]:
        if node["id"] == "B2":
            node["phases"] = [p for p in node["phases"] if p["id"] == "NS_THROUGH"]
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="not served by any phase"):
        load_city(path, settings)


def test_hospital_on_unknown_node_rejected(
    tmp_path: Path, raw_city: dict, settings: Settings
) -> None:
    broken = copy.deepcopy(raw_city)
    broken["hospitals"][0]["node_id"] = "Z9"
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="unknown node"):
        load_city(path, settings)


def test_disconnected_graph_rejected(tmp_path: Path, raw_city: dict, settings: Settings) -> None:
    broken = copy.deepcopy(raw_city)
    # Remove every road touching A1, isolating it.
    broken["roads"] = [
        r for r in broken["roads"] if r["from_node"] != "A1" and r["to_node"] != "A1"
    ]
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="not strongly connected"):
        load_city(path, settings)


def test_green_shorter_than_min_green_rejected(
    tmp_path: Path, raw_city: dict, settings: Settings
) -> None:
    broken = copy.deepcopy(raw_city)
    broken["intersections"][0]["default_plan"][0]["green_ms"] = 1000
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="shorter than"):
        load_city(path, settings)


def test_ambulance_entry_unknown_node_rejected(
    tmp_path: Path, raw_city: dict, settings: Settings
) -> None:
    broken = copy.deepcopy(raw_city)
    broken["ambulance_entry_node"] = "Z9"
    path = _write(tmp_path, broken)
    with pytest.raises(CityValidationError, match="ambulance_entry_node"):
        load_city(path, settings)
