"""The simulation engine: composes clock, RNG, controllers, traffic, demand,
ambulances, sensors, vehicle feed and network into one deterministic,
steppable world (PRD §8.6, Phase 2 Part C1).

Tick order is fixed and significant for determinism — see `step()`.
"""

from __future__ import annotations

import hashlib
import json

from controller.intersection_controller import IntersectionController
from core.clock import DEFAULT_START_LABEL, SimClock, SimTimeMs
from core.config import Settings
from core.models import City, Priority
from core.observer import SimulationObserver
from core.rng import RngFactory
from sim.ambulance import AmbulanceSim, SimAmbulance
from sim.demand import DemandModel
from sim.metrics import SimulationMetrics
from sim.network import SimNetwork
from sim.sensors import SimSensorFeed
from sim.traffic import TrafficModel
from sim.vehicle_feed import SimVehicleFeed


class Simulation:
    def __init__(
        self, city: City, settings: Settings, seed: int, start_wall_label: str = DEFAULT_START_LABEL
    ) -> None:
        self.city = city
        self.settings = settings
        self.seed = seed

        self.clock = SimClock(start_wall_label=start_wall_label)
        self.rng = RngFactory(seed)

        self.controllers: dict[str, IntersectionController] = {
            n.id: IntersectionController(
                n,
                ped_walk_ms=settings.ped_walk_ms,
                max_hold_ms=settings.max_hold_ms,
                hmac_secret=settings.hmac_secret,
                idempotency_store_size=settings.idempotency_store_size,
            )
            for n in sorted(city.intersections, key=lambda n: n.id)
        }
        self.network = SimNetwork(settings, self.rng, self.controllers)
        self.traffic = TrafficModel(city, settings, self.rng, self.controllers)
        self.demand = DemandModel(city, settings, self.rng, self.clock, self.traffic)
        self.ambulances = AmbulanceSim(city, settings, self.traffic, self.controllers)
        self.sensors = SimSensorFeed(city, settings, self.rng, self.traffic)
        self.vehicle_feed = SimVehicleFeed(settings, self.rng, self.ambulances)
        self.metrics = SimulationMetrics(city, self.controllers, self.traffic, self.ambulances)

        self.observers: list[SimulationObserver] = []
        hb_ms = settings.heartbeat_interval_ms
        self._status_next_at: dict[str, SimTimeMs] = dict.fromkeys(self.controllers, hb_ms)
        self._status_seq: dict[str, int] = dict.fromkeys(self.controllers, 0)
        self._tick_count = 0

    def add_ambulance(self, vehicle_id: str, route: list[str], priority: Priority) -> SimAmbulance:
        now = self.clock.now()
        amb = self.ambulances.add(vehicle_id, route, priority, now)
        self.vehicle_feed.register(vehicle_id, now)
        return amb

    def add_observer(self, observer: SimulationObserver) -> None:
        self.observers.append(observer)

    # -- the fixed tick order ------------------------------------------

    def step(self) -> SimTimeMs:
        dt_ms = self.settings.sim_tick_ms
        now = self.clock.advance(dt_ms)  # 1. advance clock

        self.network.step(now)  # 2. deliver due network messages

        for node_id in sorted(self.controllers):  # 3. tick signal state machines
            self.controllers[node_id].tick(now)
            self._send_due_status_report(node_id, now)

        self.traffic.discharge(now, dt_ms)  # 4. discharge queues on green
        self.traffic.move_arrivals_into_queues(now)  # 5. in-transit -> queue on arrival

        self.demand.step(now, dt_ms)  # 6. spawn new vehicles

        self.ambulances.step(now, dt_ms)  # 7. move / stop / pass

        self.sensors.step(now)  # 8a. sensor readings due this tick
        self.vehicle_feed.step(now)  # 8b. GPS fixes due this tick

        self.metrics.record(now)  # 9. record metrics
        for observer in self.observers:
            observer.on_tick(now)

        self._tick_count += 1
        return now

    def _send_due_status_report(self, node_id: str, now: SimTimeMs) -> None:
        if now < self._status_next_at[node_id]:
            return
        self._status_seq[node_id] += 1
        report = self.controllers[node_id].build_status_report(self._status_seq[node_id], now)
        self.network.send_status(report)
        self._status_next_at[node_id] += self.settings.heartbeat_interval_ms

    def run_for(self, duration_ms: int) -> None:
        ticks = duration_ms // self.settings.sim_tick_ms
        for _ in range(ticks):
            self.step()

    # -- introspection -----------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Fully JSON-serialisable state snapshot."""
        intersections = {}
        for node_id in sorted(self.controllers):
            controller = self.controllers[node_id]
            sm = controller.state_machine
            intersections[node_id] = {
                "state": str(sm.state),
                "current_phase_id": sm.current_phase_id,
                "signal_view": {str(d): str(s) for d, s in controller.signal_view().items()},
            }

        segments = {}
        for segment_id in sorted(self.traffic.segments):
            seg = self.traffic.segments[segment_id]
            segments[segment_id] = {
                "queue_length": len(seg.queue),
                "in_transit": len(seg.in_transit),
            }

        ambulances = {}
        for vehicle_id in sorted(self.ambulances.ambulances):
            amb = self.ambulances.ambulances[vehicle_id]
            ambulances[vehicle_id] = {
                "x": amb.x,
                "y": amb.y,
                "speed_mps": amb.speed_mps,
                "heading_deg": amb.heading_deg,
                "state": str(amb.state),
                "route_index": amb.route_index,
                "total_stops": amb.total_stops,
                "current_segment_id": amb.current_segment_id,
            }

        return {
            "sim_time_ms": self.clock.now(),
            "sim_time_label": self.clock.format(),
            "seed": self.seed,
            "intersections": intersections,
            "segments": segments,
            "ambulances": ambulances,
        }

    def state_hash(self) -> str:
        canonical = json.dumps(self.snapshot(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
