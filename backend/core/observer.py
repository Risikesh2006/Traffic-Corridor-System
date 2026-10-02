"""A tiny decoupling seam: `Simulation` calls observers every tick without
knowing anything about who they are (e.g. `chaos.invariants.InvariantChecker`
in a later phase) — keeps `sim/` from ever needing to import `chaos/`.
"""

from __future__ import annotations

from typing import Protocol

from core.clock import SimTimeMs


class SimulationObserver(Protocol):
    def on_tick(self, now: SimTimeMs) -> None: ...
