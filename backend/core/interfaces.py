"""Hardware abstraction boundary (PRD §8.4).

Control logic (routing, corridor, safety, controller modules) must depend only on
these protocols, never on anything under `sim/`. The simulator provides concrete
implementations; a future real deployment would provide NTCIP/GPS-backed ones
without changing any control-logic code.
"""

from __future__ import annotations

from typing import Protocol

from core.models import Command, EmergencyVehicle, SensorReading


class VehicleFeed(Protocol):
    """Real-world equivalent: GPS unit / crew phone over 4G."""

    def latest_position(self, vehicle_id: str) -> EmergencyVehicle | None: ...


class SensorFeed(Protocol):
    """Real-world equivalent: loop detectors, cameras, probe data."""

    def latest_readings(self, road_id: str) -> list[SensorReading]: ...


class SignalLink(Protocol):
    """Real-world equivalent: field controllers via NTCIP."""

    async def send_command(self, command: Command) -> bool:
        """Returns True if the controller acknowledged the command."""
        ...

    def controller_reachable(self, intersection_id: str) -> bool: ...
