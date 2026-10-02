from pathlib import Path

from core.config import CityConfig, Settings

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def test_city_loads():
    city = CityConfig.load(CONFIG_DIR / "city.json")
    assert len(city.intersections) == 36
    assert len(city.hospitals) == 1
    assert city.hospitals[0].node_id == "F1"


def test_roads_reference_valid_nodes():
    city = CityConfig.load(CONFIG_DIR / "city.json")
    node_ids = {n.id for n in city.intersections}
    for road in city.roads:
        assert road.from_node in node_ids
        assert road.to_node in node_ids
        assert road.length_m > 0


def test_conflict_matrix_present_per_intersection():
    city = CityConfig.load(CONFIG_DIR / "city.json")
    for node in city.intersections:
        assert node.conflict_matrix.conflicts_with("N", "E")
        assert not node.conflict_matrix.conflicts_with("N", "S")


def test_settings_load_defaults():
    settings = Settings.load(CONFIG_DIR / "settings.yaml")
    assert settings.amber_s == 4
    assert settings.max_hold_s == 90
    assert settings.lease_duration_s == 30
