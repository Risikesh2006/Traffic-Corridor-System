from pathlib import Path

from core.config import load_settings
from core.general_city_loader import load_general_city
from sim.topology import is_border_intersection

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
GRID_PATH = CONFIG_DIR / "cities" / "grid.city.json"


def _city():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    return load_general_city(GRID_PATH, settings)


def test_border_intersections_detected():
    city = _city()
    assert is_border_intersection(city, "A1")
    assert is_border_intersection(city, "F6")
    assert not is_border_intersection(city, "B2")
