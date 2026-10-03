"""The composition root (PRD Phase 4 Part A3, ADR-023).

The ONLY module allowed to import both the simulator and the central system.
It builds the physical world (`sim.Simulation`), builds the central system
(`central.CentralSystem`), and connects them through the interfaces in
`core/interfaces.py`: the simulator's GPS feed, sensors, presence detectors
and network up-link are handed to the central system as feeds, and the
central system's commands go back through the simulator's network.

Stepping order, on one shared clock:
  - every sim tick: the simulation advances (`Simulation.step`)
  - on the corridor cadence (corridor_tick_ms): the central system steps
    and reads whatever the simulation has produced so far

Only `api/` and `scripts/` may import this package.
"""

from __future__ import annotations

from pathlib import Path

from central.system import CentralSystem
from core.city_loader import load_city
from core.config import Settings, load_settings
from core.models import City, Priority
from sim.engine import Simulation
from state.history import HistoricalProfile

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
PROFILE_PATH = REPO_ROOT / "data" / "historical_profile.json"


class Runtime:
    def __init__(
        self,
        city: City,
        settings: Settings,
        seed: int,
        profile: HistoricalProfile | None = None,
    ) -> None:
        self.settings = settings
        self.sim = Simulation(city, settings, seed=seed)
        self.central = CentralSystem(
            city=city,
            settings=settings,
            clock=self.sim.clock,
            vehicle_feed=self.sim.vehicle_feed,
            sensor_feed=self.sim.sensors,
            status_feed=self.sim.network,
            presence_feed=self.sim.ambulances,
            signal_link=self.sim.network,
            guidance_link=self.sim.guidance,
            profile=profile,
        )
        self._corridor_tick_ms = settings.corridor_tick_ms

    def add_ambulance(self, vehicle_id: str, route: list[str], priority: Priority) -> None:
        """Fixed route, not replanned (baseline runs)."""
        self.sim.add_ambulance(vehicle_id, route, priority)
        self.central.track_vehicle(vehicle_id, priority, route, managed=False)

    def dispatch(
        self, vehicle_id: str, priority: Priority, hospital_id: str | None = None
    ) -> list[str] | None:
        """Plan the first route from the ambulance entry node and start the trip."""
        route = self.central.dispatch(vehicle_id, priority, hospital_id)
        if route is not None:
            self.sim.add_ambulance(vehicle_id, route, priority)
        return route

    def step(self) -> None:
        now = self.sim.step()
        if now % self._corridor_tick_ms == 0:
            self.central.step(now)

    def run_for(self, duration_ms: int) -> None:
        ticks = duration_ms // self.settings.sim_tick_ms
        for _ in range(ticks):
            self.step()


def load_profile_if_present() -> HistoricalProfile | None:
    if PROFILE_PATH.exists():
        return HistoricalProfile.load(PROFILE_PATH)
    return None


def build_runtime(
    seed: int | None = None,
    settings: Settings | None = None,
    peak_multiplier: float | None = None,
) -> Runtime:
    settings = settings or load_settings(CONFIG_DIR / "settings.yaml")
    if peak_multiplier is not None:
        settings = settings.model_copy(update={"demand_peak_multiplier": peak_multiplier})
    city = load_city(CONFIG_DIR / "city.json", settings)
    return Runtime(
        city=city,
        settings=settings,
        seed=settings.default_seed if seed is None else seed,
        profile=load_profile_if_present(),
    )
