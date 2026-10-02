"""Realtime pacing for live demos (PRD Phase 2 Part C2). The headless path
(`Simulation.run_for`) never touches this — it has no dependency on wall
time at all. This is purely a UI/demo convenience.
"""

from __future__ import annotations

import asyncio

from core.clock import WallPacer
from sim.engine import Simulation


class RealtimeRunner:
    def __init__(self, simulation: Simulation, speed_multiplier: float = 1.0) -> None:
        self.simulation = simulation
        self.pacer = WallPacer(speed_multiplier)
        self.paused = False
        self._stopped = True

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def set_speed(self, multiplier: float) -> None:
        self.pacer.set_speed(multiplier)

    def stop(self) -> None:
        self._stopped = True

    async def run(self, poll_interval_s: float = 0.05) -> None:
        """Runs until `stop()` is called. Steps the simulation in whole
        tick increments, carrying over any fractional real-time remainder
        so the pace stays accurate over a long run."""
        self._stopped = False
        tick_ms = self.simulation.settings.sim_tick_ms
        carry_ms = 0.0
        self.pacer.reset()

        while not self._stopped:
            elapsed_ms = self.pacer.sim_ms_elapsed()
            if not self.paused:
                carry_ms += elapsed_ms
                while carry_ms >= tick_ms:
                    self.simulation.step()
                    carry_ms -= tick_ms
            await asyncio.sleep(poll_interval_s)
