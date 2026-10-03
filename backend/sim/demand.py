"""Background traffic demand (PRD FR-SIM-03): vehicles enter at the city
boundary and from a small internal "parking" source per segment, at a rate
drawn from a time-of-day profile (`Settings.demand_rate_vph`).

Each sim tick is treated as one Bernoulli trial per source: P(arrival) =
rate_vph / 3_600_000 * dt_ms. This approximates a Poisson process — accurate
as long as the per-tick arrival probability stays small, which holds for our
tick size (100 ms default) and demand rates. Multiple arrivals from the same
source in one tick are not modelled; at realistic rates this is negligible.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.clock import Clock, SimTimeMs
from core.config import Settings
from core.general_city import GeneralCity
from core.rng import RngFactory
from sim.topology import is_border_intersection
from sim.traffic import TrafficModel


@dataclass(frozen=True)
class DemandSource:
    segment_id: str
    is_arterial: bool
    is_boundary: bool


def _is_arterial(city: GeneralCity, segment_id: str) -> bool:
    return city.segment(segment_id).lanes > 2


def build_sources(city: GeneralCity) -> list[DemandSource]:
    sources = []
    for road in sorted(city.roads, key=lambda r: r.id):
        boundary = is_border_intersection(city, road.from_node)
        sources.append(
            DemandSource(
                segment_id=road.id, is_arterial=_is_arterial(city, road.id), is_boundary=boundary
            )
        )
    return sources


class DemandModel:
    def __init__(
        self, city: GeneralCity, settings: Settings, rng: RngFactory, clock: Clock, traffic: TrafficModel
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.traffic = traffic
        self._rng = rng.stream("demand")
        self.sources = build_sources(city)
        self.total_attempted = 0

    def _hour_of_day(self, now: SimTimeMs) -> int:
        return int(self.clock.format(now)[:2])

    def _rate_vph(self, source: DemandSource, hour: int) -> float:
        if source.is_boundary:
            return self.settings.demand_rate_vph(hour, source.is_arterial)
        multiplier = self.settings.demand_hourly_multipliers[hour % 24]
        rate = self.settings.demand_internal_source_vph * multiplier
        return rate * self.settings.demand_peak_multiplier

    def step(self, now: SimTimeMs, dt_ms: int) -> None:
        hour = self._hour_of_day(now)
        for source in self.sources:
            rate_vph = self._rate_vph(source, hour)
            if rate_vph <= 0:
                continue
            p_arrival = rate_vph / 3_600_000 * dt_ms
            if self._rng.random() < p_arrival:
                self.total_attempted += 1
                self.traffic.spawn(source.segment_id, now)
