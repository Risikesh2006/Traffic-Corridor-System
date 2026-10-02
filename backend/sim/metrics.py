"""Per-run metrics, recorded once per tick (PRD Phase 2 Part C3)."""

from __future__ import annotations

from controller.intersection_controller import IntersectionController
from core.clock import SimTimeMs
from core.models import City
from sim.ambulance import AmbulanceSim, SimAmbulance
from sim.traffic import TrafficModel


class SimulationMetrics:
    def __init__(
        self,
        city: City,
        controllers: dict[str, IntersectionController],
        traffic: TrafficModel,
        ambulances: AmbulanceSim,
    ) -> None:
        self.city = city
        self.controllers = controllers
        self.traffic = traffic
        self.ambulances = ambulances

        self._queue_sum: dict[str, int] = dict.fromkeys(traffic.segments, 0)
        self._queue_count: dict[str, int] = dict.fromkeys(traffic.segments, 0)
        self._queue_max: dict[str, int] = dict.fromkeys(traffic.segments, 0)
        self._max_red_ms: dict[tuple[str, str], int] = {}

    def record(self, now: SimTimeMs) -> None:
        for segment_id, seg in self.traffic.segments.items():
            q = len(seg.queue)
            self._queue_sum[segment_id] += q
            self._queue_count[segment_id] += 1
            if q > self._queue_max[segment_id]:
                self._queue_max[segment_id] = q

        for node_id, controller in self.controllers.items():
            for direction, since in controller.state_machine.red_since.items():
                duration = now - since
                key = (node_id, str(direction))
                if duration > self._max_red_ms.get(key, 0):
                    self._max_red_ms[key] = duration

    # -- queries --------------------------------------------------------

    def queue_summary(self) -> list[tuple[str, float, int]]:
        """[(segment_id, average_queue_length, max_queue_length), ...]"""
        out = []
        for segment_id, total in self._queue_sum.items():
            count = self._queue_count[segment_id]
            avg = total / count if count else 0.0
            out.append((segment_id, avg, self._queue_max[segment_id]))
        return out

    def top_longest_queues(self, n: int = 10) -> list[tuple[str, float, int]]:
        return sorted(self.queue_summary(), key=lambda row: row[1], reverse=True)[:n]

    def max_red_time_ms(self) -> dict[tuple[str, str], int]:
        return dict(self._max_red_ms)

    def worst_hold(self) -> tuple[str, str, int] | None:
        if not self._max_red_ms:
            return None
        (node_id, direction), duration = max(self._max_red_ms.items(), key=lambda kv: kv[1])
        return node_id, direction, duration

    def average_delay_ms(self) -> float:
        return self.traffic.average_queue_delay_ms()

    def vehicle_conservation(self) -> tuple[int, int, int]:
        """(spawned, exited, currently_in_network)."""
        return (
            self.traffic.total_spawned,
            self.traffic.total_exited,
            self.traffic.vehicles_in_network(),
        )

    def ambulance_summary(self, vehicle_id: str) -> dict[str, object]:
        amb: SimAmbulance = self.ambulances.ambulances[vehicle_id]
        travel_time_ms = (
            (amb.arrival_time - amb.start_time)
            if amb.arrival_time is not None and amb.start_time is not None
            else None
        )
        return {
            "vehicle_id": vehicle_id,
            "travel_time_ms": travel_time_ms,
            "stops": amb.total_stops,
            "stopped_time_ms": amb.total_stopped_time_ms,
            "section_times_ms": dict(amb.section_times_ms),
            "arrived": amb.arrival_time is not None,
        }
