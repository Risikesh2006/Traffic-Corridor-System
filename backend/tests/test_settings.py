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
