import json
from pathlib import Path

import pytest

from core.clock import SimClock
from state.history import HistoricalProfile

SEG = "A1->A2"


def _profile(
    tmp_path: Path, buckets: list[dict[str, float]], bucket_minutes: int = 15
) -> HistoricalProfile:
    data = {
        "metadata": {"seeds": [1], "bucket_minutes": bucket_minutes},
        "bucket_minutes": bucket_minutes,
        "segments": {SEG: buckets},
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return HistoricalProfile.load(path)


def _flat_buckets(n: int, density_by_index) -> list[dict[str, float]]:
    return [
        {"density": density_by_index(i), "queue": density_by_index(i) / 2, "dispersion": 0.1}
        for i in range(n)
    ]


def test_lookup_exact_bucket_centre_returns_that_bucket(tmp_path: Path) -> None:
    buckets = _flat_buckets(96, lambda i: float(i))
    profile = _profile(tmp_path, buckets)
    clock = SimClock(start_wall_label="00:00:00")
    # 00:30 is exactly bucket 2 (30 / 15).
    prior = profile.lookup(SEG, sim_time=30 * 60_000, clock=clock)
    assert prior.density == pytest.approx(2.0)


def test_lookup_interpolates_between_buckets(tmp_path: Path) -> None:
    buckets = _flat_buckets(96, lambda i: float(i))
    profile = _profile(tmp_path, buckets)
    clock = SimClock(start_wall_label="00:00:00")
    # 00:22:30 is halfway between bucket 1 (00:15) and bucket 2 (00:30).
    prior = profile.lookup(SEG, sim_time=22 * 60_000 + 30_000, clock=clock)
    assert prior.density == pytest.approx(1.5)
    assert prior.queue == pytest.approx(0.75)


def test_lookup_wraps_across_midnight(tmp_path: Path) -> None:
    buckets = _flat_buckets(96, lambda i: float(i))
    profile = _profile(tmp_path, buckets)
    clock = SimClock(start_wall_label="23:52:30")
    # 23:52:30 sits halfway between bucket 95 (23:45, density 95) and
    # bucket 0 (00:00, density 0), wrapping past midnight.
    prior = profile.lookup(SEG, sim_time=0, clock=clock)
    assert prior.density == pytest.approx(47.5)


def test_unknown_segment_returns_zero_prior(tmp_path: Path) -> None:
    profile = _profile(tmp_path, _flat_buckets(96, lambda i: 1.0))
    clock = SimClock()
    prior = profile.lookup("NO->SUCH", sim_time=0, clock=clock)
    assert prior.density == 0.0
