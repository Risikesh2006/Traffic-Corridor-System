"""Historical profile loader (PRD Phase 4 Part C2).

Real systems have months of historical data; `scripts/build_history.py`
generates ours deterministically from the simulator (see ADR). This module
only loads and interpolates the result — it has no simulator dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.clock import Clock, SimTimeMs

BUCKETS_PER_DAY_DEFAULT = 96  # 24h * 60 / 15-minute buckets


@dataclass(frozen=True)
class Prior:
    density: float
    queue: float
    dispersion: float


@dataclass(frozen=True)
class _BucketStats:
    density: float
    queue: float
    dispersion: float


class HistoricalProfile:
    def __init__(
        self,
        segments: dict[str, list[_BucketStats]],
        bucket_minutes: int,
        metadata: dict[str, object],
    ) -> None:
        self._segments = segments
        self._bucket_minutes = bucket_minutes
        self._buckets_per_day = (24 * 60) // bucket_minutes
        self.metadata = metadata

    @classmethod
    def load(cls, path: str | Path) -> HistoricalProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        bucket_minutes = data["bucket_minutes"]
        segments = {
            segment_id: [_BucketStats(b["density"], b["queue"], b["dispersion"]) for b in buckets]
            for segment_id, buckets in data["segments"].items()
        }
        return cls(segments, bucket_minutes, data["metadata"])

    def lookup(self, segment_id: str, sim_time: SimTimeMs, clock: Clock) -> Prior:
        """Interpolates between the two nearest bucket centres for the
        time-of-day `sim_time` maps to via `clock`."""
        buckets = self._segments.get(segment_id)
        if not buckets:
            return Prior(density=0.0, queue=0.0, dispersion=0.0)

        minute_of_day = self._minute_of_day(sim_time, clock)
        bucket_width = self._bucket_minutes
        exact_index = minute_of_day / bucket_width
        n = len(buckets)

        lo = int(exact_index) % n
        hi = (lo + 1) % n
        frac = exact_index - int(exact_index)

        a, b = buckets[lo], buckets[hi]
        return Prior(
            density=a.density + (b.density - a.density) * frac,
            queue=a.queue + (b.queue - a.queue) * frac,
            dispersion=a.dispersion + (b.dispersion - a.dispersion) * frac,
        )

    def _minute_of_day(self, sim_time: SimTimeMs, clock: Clock) -> float:
        label = clock.format(sim_time)  # "HH:MM:SS.mmm"
        hours, minutes, rest = label.split(":")
        seconds = float(rest)
        return int(hours) * 60 + int(minutes) + seconds / 60.0
