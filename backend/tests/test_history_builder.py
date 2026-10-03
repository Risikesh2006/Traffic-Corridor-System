import sys
from pathlib import Path

from core.city_loader import load_city
from core.config import load_settings

BACKEND = Path(__file__).resolve().parent.parent
CONFIG_DIR = BACKEND.parent / "config"
sys.path.insert(0, str(BACKEND / "scripts"))

import build_history  # noqa: E402


def test_builder_is_reproducible_for_the_same_seeds() -> None:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    duration_ms = 30 * 60 * 1000  # 30 simulated minutes keeps the test fast
    first = build_history._run_seed(4417, settings, city, duration_ms)
    second = build_history._run_seed(4417, settings, city, duration_ms)
    assert first == second


def test_merge_is_order_independent_for_seeds_that_are_merged_in_seed_order() -> None:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_city(CONFIG_DIR / "city.json", settings)
    n = (24 * 60) // settings.history_bucket_minutes
    table = build_history._run_seed(1, settings, city, 20 * 60 * 1000)
    merged = build_history._merge([table], city, n)
    some_segment = sorted(merged)[0]
    assert len(merged[some_segment]) == n
