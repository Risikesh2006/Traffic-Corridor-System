"""Hardware abstraction boundary (PRD §8.4).

Control-system modules (state, tracking, routing, corridor, safety,
controller, health, events) depend only on these Protocols, never on
anything under `backend/sim/`. The simulator provides concrete
implementations today; a real deployment would provide NTCIP/GPS-backed
ones without any control-logic code changing.
"""

from __future__ import annotations

from typing import Protocol

from core.models import Command, CommandAck, ControllerStatus, GpsFix, SensorReading


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
