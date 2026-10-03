"""Hardware abstraction boundary (PRD §8.4).

Control-system modules (state, tracking, routing, corridor, safety,
controller, health, events) depend only on these Protocols, never on
anything under `backend/sim/`. The simulator provides concrete
implementations today; a real deployment would provide NTCIP/GPS-backed
ones without any control-logic code changing.
"""

from __future__ import annotations

from typing import Protocol

from core.models import (
    Command,
    CommandAck,
    ControllerStatus,
    GpsFix,
    PresenceReading,
    RouteAck,
    SensorReading,
    StatusReport,
)


class VehicleFeed(Protocol):
    """Today: the simulated ambulance's position generator.
    Real world: a GPS unit or crew phone reporting over 4G.
    """

    def latest_fix(self, vehicle_id: str) -> GpsFix | None:
        """Most recent known fix for `vehicle_id`, or None if never seen."""
        ...

    def drain_fixes(self) -> list[GpsFix]:
        """Return and clear all fixes received since the last call."""
        ...


class SensorFeed(Protocol):
    """Today: simulated density sensors with configurable noise.
    Real world: loop detectors, cameras, or probe-vehicle data.
    """

    def drain_readings(self) -> list[SensorReading]:
        """Return and clear all sensor readings received since the last call."""
        ...


class SignalLink(Protocol):
    """Today: `SimNetwork` plus simulated intersection controllers.
    Real world: field controllers reachable via NTCIP.
    """

    async def send(self, command: Command) -> None:
        """Dispatch a command; delivery is not guaranteed (network may drop it)."""
        ...

    def drain_acks(self) -> list[CommandAck]:
        """Return and clear all controller acknowledgements received since the last call."""
        ...

    def controller_status(self, intersection_id: str) -> ControllerStatus:
        """Best-known reachability/operating status of that intersection's controller."""
        ...


class StatusFeed(Protocol):
    """Today: the SimNetwork up-link carrying controller status reports.
    Real world: the same reports arriving over the field network."""

    def drain_status_reports(self) -> list[StatusReport]:
        """Return and clear all status reports delivered since the last call."""
        ...


class PresenceFeed(Protocol):
    """Today: simulated stop-line emergency-vehicle presence detectors.
    Real world: optical or acoustic emergency-vehicle detectors."""

    def drain_presence_readings(self) -> list[PresenceReading]:
        """Return and clear all presence readings since the last call."""
        ...


class GuidanceLink(Protocol):
    """Central -> vehicle route delivery.
    Today: the simulated ambulance (sim/guidance.py).
    Real world: the crew's navigation app over a data link.
    """

    def send_route(self, vehicle_id: str, route_version: int, intersections: list[str]) -> None:
        """Push a full route (intersection IDs from the vehicle's current departure onward)."""
        ...

    def drain_acks(self) -> list[RouteAck]:
        """Return and clear all route acknowledgements since the last call."""
        ...
