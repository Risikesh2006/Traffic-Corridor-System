"""Simulated density sensors implementing `SensorFeed` (PRD FR-SIM-06).

Every `sensor_interval_s`, each road segment reports density (vehicles in
queue + in-transit, per km per lane) with Gaussian noise, its own sequence
number, and the current sim time. Fault controls (`freeze`/`unfreeze`/
`set_noise`) exist for the chaos-testing phase.
"""

from __future__ import annotations

from core.clock import SimTimeMs
from core.config import Settings
from core.models import City, SensorMetric, SensorReading
from core.rng import RngFactory
from sim.traffic import TrafficModel


class SimSensorFeed:
    def __init__(
        self, city: City, settings: Settings, rng: RngFactory, traffic: TrafficModel
    ) -> None:
        self.settings = settings
        self.traffic = traffic
        self._noise_rng = rng.stream("sensor_noise")
        self._noise_std = settings.sensor_density_noise_std
        self._interval_ms = settings.sensor_interval_ms

        self._segment_ids = sorted(city.roads, key=lambda r: r.id)
        self._length_km_lane: dict[str, float] = {
            r.id: (r.length_m / 1000.0) * r.lanes for r in city.roads
        }
        self._next_emit_at: dict[str, SimTimeMs] = {r.id: self._interval_ms for r in city.roads}
        self._sequence: dict[tuple[str, SensorMetric], int] = {}
        self._frozen: set[str] = set()
        self._pending: list[SensorReading] = []

    def freeze(self, segment_ids: list[str]) -> None:
        self._frozen.update(segment_ids)

    def unfreeze(self, segment_ids: list[str]) -> None:
        self._frozen.difference_update(segment_ids)

    def set_noise(self, std_dev: float) -> None:
        self._noise_std = std_dev

    def step(self, now: SimTimeMs) -> None:
        for road in self._segment_ids:
            segment_id = road.id
            while self._next_emit_at[segment_id] <= now:
                if segment_id not in self._frozen:
                    self._emit(segment_id, self._next_emit_at[segment_id])
                self._next_emit_at[segment_id] += self._interval_ms

    def _emit(self, segment_id: str, at: SimTimeMs) -> None:
        seg = self.traffic.segments[segment_id]
        vehicles = len(seg.queue) + len(seg.in_transit)
        km_lane = self._length_km_lane[segment_id]
        true_density = vehicles / km_lane if km_lane > 0 else 0.0
        self._append(segment_id, SensorMetric.DENSITY, true_density, at)
        self._append(segment_id, SensorMetric.QUEUE_COUNT, float(len(seg.queue)), at)

    def _append(
        self, segment_id: str, metric: SensorMetric, true_value: float, at: SimTimeMs
    ) -> None:
        noise = self._noise_rng.gauss(0, self._noise_std)
        value = max(0.0, true_value + noise)
        self._sequence[(segment_id, metric)] = self._sequence.get((segment_id, metric), 0) + 1
        self._pending.append(
            SensorReading(
                source_id=f"sensor-{segment_id}",
                road_id=segment_id,
                metric=metric,
                value=value,
                timestamp=at,
                sequence=self._sequence[(segment_id, metric)],
                confidence=1.0,
            )
        )

    # -- SensorFeed interface -----------------------------------------------

    def drain_readings(self) -> list[SensorReading]:
        readings, self._pending = self._pending, []
        return readings
