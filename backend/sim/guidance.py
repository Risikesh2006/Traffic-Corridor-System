"""Simulated route delivery to ambulances (PRD Phase 5 Part A2).

Stands in for the crew's navigation app. A route pushed by central code
travels over a dedicated link with fixed latency, waits a reaction delay on
the vehicle side, and is then adopted by the simulated ambulance if the
ambulance has not already committed past the point where the routes diverge.
Older versions never overwrite newer ones, and every outcome is acknowledged.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.clock import Clock, SimTimeMs
from core.config import Settings
from core.models import RouteAck, RouteAckStatus
from sim.ambulance import AmbulanceSim


@dataclass(frozen=True)
class _InFlight:
    deliver_at: SimTimeMs
    vehicle_id: str
    route_version: int
    intersections: tuple[str, ...]


@dataclass(frozen=True)
class _Pending:
    adopt_at: SimTimeMs
    route_version: int
    intersections: tuple[str, ...]


class SimGuidance:
    def __init__(self, ambulances: AmbulanceSim, settings: Settings, clock: Clock) -> None:
        self._ambulances = ambulances
        self._settings = settings
        self._clock = clock
        self._in_flight: list[_InFlight] = []
        self._pending: dict[str, _Pending] = {}
        self._adopted_version: dict[str, int] = {}
        self._acks: list[RouteAck] = []

    def send_route(self, vehicle_id: str, route_version: int, intersections: list[str]) -> None:
        now = self._clock.now()
        self._in_flight.append(
            _InFlight(
                deliver_at=now + self._settings.guidance_latency_ms,
                vehicle_id=vehicle_id,
                route_version=route_version,
                intersections=tuple(intersections),
            )
        )

    def drain_acks(self) -> list[RouteAck]:
        acks, self._acks = self._acks, []
        return acks

    def step(self, now: SimTimeMs) -> None:
        still_in_flight: list[_InFlight] = []
        for message in self._in_flight:
            if message.deliver_at > now:
                still_in_flight.append(message)
                continue
            current = self._pending.get(message.vehicle_id)
            if current is not None and current.route_version >= message.route_version:
                continue
            self._pending[message.vehicle_id] = _Pending(
                adopt_at=now + self._settings.reaction_delay_ms,
                route_version=message.route_version,
                intersections=message.intersections,
            )
        self._in_flight = still_in_flight

        for vehicle_id in sorted(self._pending):
            pending = self._pending[vehicle_id]
            if pending.adopt_at > now:
                continue
            del self._pending[vehicle_id]
            self._adopt(vehicle_id, pending, now)

    def _adopt(self, vehicle_id: str, pending: _Pending, now: SimTimeMs) -> None:
        if pending.route_version <= self._adopted_version.get(vehicle_id, 0):
            self._record(
                vehicle_id, pending.route_version, RouteAckStatus.IGNORED_STALE_VERSION, now
            )
            return
        if self._ambulances.adopt_route(vehicle_id, list(pending.intersections)):
            self._adopted_version[vehicle_id] = pending.route_version
            self._record(vehicle_id, pending.route_version, RouteAckStatus.ADOPTED, now)
            return
        self._record(
            vehicle_id,
            pending.route_version,
            RouteAckStatus.CANNOT_FOLLOW,
            now,
            reason_code="DIVERGENCE_ALREADY_PASSED",
        )

    def _record(
        self,
        vehicle_id: str,
        route_version: int,
        status: RouteAckStatus,
        now: SimTimeMs,
        reason_code: str | None = None,
    ) -> None:
        self._acks.append(
            RouteAck(
                vehicle_id=vehicle_id,
                route_version=route_version,
                status=status,
                reason_code=reason_code,
                timestamp=now,
            )
        )
