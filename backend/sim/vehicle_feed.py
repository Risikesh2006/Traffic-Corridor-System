"""Simulated GPS feed implementing `VehicleFeed` (PRD FR-SIM-05/FR-TRK).

Emits a GPS fix per tracked ambulance every `gps_interval_s`, with Gaussian
position noise. Fault controls (`gps_off`/`gps_on`/`inject_jump`) exist for
later GPS-loss and bad-data-rejection tests.

Coordinates are city metres (x, y); `xy_to_latlng`/`latlng_to_xy` are pure,
standalone equirectangular-projection helpers anchored at a configurable
origin, for a future real-phone-GPS integration — they don't depend on any
simulator state.
"""

from __future__ import annotations

import math

from core.clock import SimTimeMs
from core.config import Settings
from core.models import GpsFix
from core.rng import RngFactory
from sim.ambulance import AmbulanceSim

_EARTH_RADIUS_M = 6_371_000.0


def xy_to_latlng(x: float, y: float, origin_lat: float, origin_lng: float) -> tuple[float, float]:
    """y increases southward in city coordinates (PRD grid convention), so
    north is -y."""
    lat = origin_lat - (y / _EARTH_RADIUS_M) * (180.0 / math.pi)
    lng = origin_lng + (x / (_EARTH_RADIUS_M * math.cos(math.radians(origin_lat)))) * (
        180.0 / math.pi
    )
    return lat, lng


def latlng_to_xy(
    lat: float, lng: float, origin_lat: float, origin_lng: float
) -> tuple[float, float]:
    y = (origin_lat - lat) * (math.pi / 180.0) * _EARTH_RADIUS_M
    x = (
        (lng - origin_lng)
        * (math.pi / 180.0)
        * _EARTH_RADIUS_M
        * math.cos(math.radians(origin_lat))
    )
    return x, y


class SimVehicleFeed:
    def __init__(self, settings: Settings, rng: RngFactory, ambulance_sim: AmbulanceSim) -> None:
        self.settings = settings
        self.ambulance_sim = ambulance_sim
        self._noise_rng = rng.stream("gps_noise")
        self._interval_ms = settings.gps_interval_ms
        self._noise_std = settings.gps_noise_std_m

        self._next_emit_at: dict[str, SimTimeMs] = {}
        self._sequence: dict[str, int] = {}
        self._enabled: dict[str, bool] = {}
        self._pending_jump_m: dict[str, float] = {}
        self._latest: dict[str, GpsFix] = {}
        self._pending: list[GpsFix] = []

    def register(self, vehicle_id: str, now: SimTimeMs) -> None:
        """Starts GPS reporting for `vehicle_id`: the first fix is emitted
        one `gps_interval_s` after `now`. Called once when a vehicle is
        added to the simulation (idempotent)."""
        if vehicle_id not in self._next_emit_at:
            self._next_emit_at[vehicle_id] = now + self._interval_ms
            self._sequence[vehicle_id] = 0
            self._enabled.setdefault(vehicle_id, True)

    def gps_off(self, vehicle_id: str) -> None:
        self._enabled[vehicle_id] = False

    def gps_on(self, vehicle_id: str) -> None:
        self._enabled[vehicle_id] = True

    def inject_jump(self, vehicle_id: str, metres: float) -> None:
        """Adds `metres` to the next emitted fix's x coordinate — a one-shot
        implausible jump, for testing GPS validation/rejection."""
        self._pending_jump_m[vehicle_id] = metres

    def step(self, now: SimTimeMs) -> None:
        for vehicle_id, amb in sorted(self.ambulance_sim.ambulances.items()):
            if vehicle_id not in self._next_emit_at:
                continue  # not registered yet
            while self._next_emit_at[vehicle_id] <= now:
                at = self._next_emit_at[vehicle_id]
                if self._enabled[vehicle_id]:
                    self._emit(vehicle_id, amb.x, amb.y, amb.speed_mps, amb.heading_deg, at)
                self._next_emit_at[vehicle_id] += self._interval_ms

    def _emit(
        self, vehicle_id: str, x: float, y: float, speed: float, heading: float, at: SimTimeMs
    ) -> None:
        jump = self._pending_jump_m.pop(vehicle_id, 0.0)
        noisy_x = x + self._noise_rng.gauss(0, self._noise_std) + jump
        noisy_y = y + self._noise_rng.gauss(0, self._noise_std)
        self._sequence[vehicle_id] += 1
        fix = GpsFix(
            vehicle_id=vehicle_id,
            x=noisy_x,
            y=noisy_y,
            speed_mps=speed,
            heading_deg=heading,
            timestamp=at,
            sequence=self._sequence[vehicle_id],
        )
        self._latest[vehicle_id] = fix
        self._pending.append(fix)

    # -- VehicleFeed interface -----------------------------------------------

    def latest_fix(self, vehicle_id: str) -> GpsFix | None:
        return self._latest.get(vehicle_id)

    def drain_fixes(self) -> list[GpsFix]:
        fixes, self._pending = self._pending, []
        return fixes
