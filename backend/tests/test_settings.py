from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import Settings, load_settings

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def test_loads_defaults_from_yaml() -> None:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    assert settings.amber_s == 4
    assert settings.max_hold_s == 90
    assert settings.default_seed == 4417


def test_ms_conversion() -> None:
    settings = Settings()
    assert settings.amber_ms == 4000
    assert settings.min_green_ms == 10_000
    assert settings.sim_tick_ms == 100


def test_amber_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(amber_s=1)


def test_hysteresis_rel_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(hysteresis_rel=1.5)


def test_all_red_must_be_shorter_than_min_green() -> None:
    with pytest.raises(ValidationError):
        Settings(all_red_s=20, min_green_s=10)


def test_heartbeat_timeout_must_exceed_interval() -> None:
    with pytest.raises(ValidationError):
        Settings(heartbeat_interval_s=5, heartbeat_timeout_s=3)


def test_negative_values_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(max_hold_s=-1)


def test_turn_probabilities_must_sum_to_one() -> None:
    with pytest.raises(ValidationError):
        Settings(turn_through_prob=0.5, turn_left_prob=0.5, turn_right_prob=0.5)


def test_demand_hourly_multipliers_must_have_24_entries() -> None:
    with pytest.raises(ValidationError):
        Settings(demand_hourly_multipliers=[1.0, 1.0])


def test_demand_rate_vph_applies_profile_peak_and_arterial() -> None:
    settings = Settings(demand_base_vph=100, demand_arterial_multiplier=2.0)
    normal = settings.demand_rate_vph(hour_of_day=8, is_arterial=False)
    arterial = settings.demand_rate_vph(hour_of_day=8, is_arterial=True)
    assert normal == pytest.approx(100 * settings.demand_hourly_multipliers[8])
    assert arterial == pytest.approx(normal * 2.0)


def test_demand_rate_vph_hour_wraps_modulo_24() -> None:
    settings = Settings()
    assert settings.demand_rate_vph(8, False) == settings.demand_rate_vph(32, False)
