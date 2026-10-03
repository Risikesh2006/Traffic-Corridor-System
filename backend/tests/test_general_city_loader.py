import json
from pathlib import Path

import pytest

from core.config import load_settings
from core.general_city_loader import GeneralCityValidationError, load_general_city

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
GRID_PATH = CONFIG_DIR / "cities" / "grid.city.json"


def _settings():
    return load_settings(CONFIG_DIR / "settings.yaml")


def _raw() -> dict:
    return json.loads(GRID_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "city.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_generated_grid_loads_successfully():
    city = load_general_city(GRID_PATH, _settings())
    assert len(city.junctions) == 36
    assert len(city.segments) == 120
    assert len(city.hospitals) == 2


def test_strongly_connected_and_hospitals_resolve():
    city = load_general_city(GRID_PATH, _settings())
    for hospital in city.hospitals:
        city.junction(hospital.node_id)  # raises KeyError if unknown


def test_duplicate_junction_id_rejected(tmp_path: Path):
    data = _raw()
    data["junctions"].append(data["junctions"][0])
    with pytest.raises(GeneralCityValidationError, match="duplicate junction"):
        load_general_city(_write(tmp_path, data), _settings())


def test_segment_referencing_unknown_node_rejected(tmp_path: Path):
    data = _raw()
    data["segments"][0]["to_node"] = "NOPE"
    with pytest.raises(GeneralCityValidationError, match="unknown to_node"):
        load_general_city(_write(tmp_path, data), _settings())


def test_approach_referencing_unknown_segment_rejected(tmp_path: Path):
    data = _raw()
    data["junctions"][0]["approaches"][0]["segment_id"] = "NOPE"
    with pytest.raises(GeneralCityValidationError, match="unknown segment"):
        load_general_city(_write(tmp_path, data), _settings())


def test_movement_referencing_unknown_approach_rejected(tmp_path: Path):
    data = _raw()
    data["junctions"][0]["movements"][0]["approach_id"] = "NOPE"
    with pytest.raises(GeneralCityValidationError, match="unknown approach"):
        load_general_city(_write(tmp_path, data), _settings())


def test_phase_with_conflicting_movements_rejected(tmp_path: Path):
    data = _raw()
    junction = next(j for j in data["junctions"] if j["id"] == "B2")
    # Force a phase onto two movements that definitely conflict (crossing throughs).
    through_ids = [m["id"] for m in junction["movements"] if m["turn_type"] == "THROUGH"]
    junction["phases"][0]["movement_ids"] = sorted(through_ids)
    with pytest.raises(GeneralCityValidationError, match="conflicting movements"):
        load_general_city(_write(tmp_path, data), _settings())


def test_unserved_movement_rejected(tmp_path: Path):
    data = _raw()
    junction = next(j for j in data["junctions"] if j["id"] == "B2")
    junction["phases"] = [junction["phases"][0]]
    with pytest.raises(GeneralCityValidationError, match="not served"):
        load_general_city(_write(tmp_path, data), _settings())


def test_hospital_on_unknown_node_rejected(tmp_path: Path):
    data = _raw()
    data["hospitals"][0]["node_id"] = "NOPE"
    with pytest.raises(GeneralCityValidationError, match="unknown node"):
        load_general_city(_write(tmp_path, data), _settings())


def _minimal_junction(junction_id: str) -> dict:
    return {
        "id": junction_id,
        "kind": "UNSIGNALISED",
        "x": 0.0,
        "y": 0.0,
        "approaches": [],
        "exits": [],
        "movements": [],
        "phases": [],
    }


def _minimal_segment(sid: str, from_node: str, to_node: str) -> dict:
    return {
        "id": sid,
        "from_node": from_node,
        "to_node": to_node,
        "length_m": 100.0,
        "lanes": 1,
        "free_flow_speed_mps": 10.0,
        "capacity_vph": 500.0,
    }


def test_disconnected_graph_rejected(tmp_path: Path):
    data = {
        "junctions": [_minimal_junction("X"), _minimal_junction("Y"), _minimal_junction("Z")],
        "segments": [
            _minimal_segment("X->Y", "X", "Y"),
            _minimal_segment("Y->X", "Y", "X"),
            # Z has no segments at all: unreachable in both directions.
        ],
        "hospitals": [],
        "ambulance_entry_node": "X",
    }
    with pytest.raises(GeneralCityValidationError, match="not strongly connected"):
        load_general_city(_write(tmp_path, data), _settings())


def test_phase_shorter_than_settings_min_green_rejected(tmp_path: Path):
    data = _raw()
    data["junctions"][0]["phases"][0]["min_green_ms"] = 1
    with pytest.raises(GeneralCityValidationError, match="min_green_ms"):
        load_general_city(_write(tmp_path, data), _settings())


def test_unknown_ambulance_entry_node_rejected(tmp_path: Path):
    data = _raw()
    data["ambulance_entry_node"] = "NOPE"
    with pytest.raises(GeneralCityValidationError, match="ambulance_entry_node"):
        load_general_city(_write(tmp_path, data), _settings())
