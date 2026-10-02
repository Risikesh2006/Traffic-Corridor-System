"""The simulated clock.

This is the ONLY file in the control system permitted to reference real
wall-clock time (and only inside `RealtimePacer`, which is a display/demo
convenience, never a source of truth for `SimClock`). Every other module
reads time through the `Clock` protocol so that a run is fully reproducible
from its seed and inputs (PRD §8.7, ADR-001).
"""

from __future__ import annotations

import time as _real_time
from typing import Protocol

SimTimeMs = int

# Default sim start: 14:00:00 on a (notional) weekday, chosen to match the
# PRD's reference scenario of peak-hour afternoon traffic (§19.3).
DEFAULT_START_LABEL = "14:00:00"


class Clock(Protocol):
    def now(self) -> SimTimeMs: ...
    def format(self, t: SimTimeMs | None = None) -> str: ...


class SimClock:
    """Monotonic simulated clock. Time only moves via explicit `advance()`."""

    def __init__(self, start_wall_label: str = DEFAULT_START_LABEL) -> None:
        self._now_ms: SimTimeMs = 0
        self.start_wall_label = start_wall_label

    def now(self) -> SimTimeMs:
        return self._now_ms

    def now_seconds(self) -> float:
        return self._now_ms / 1000.0

    def advance(self, dt_ms: int) -> SimTimeMs:
        if dt_ms < 0:
            raise ValueError(f"advance() requires a non-negative delta, got {dt_ms}")
        self._now_ms += dt_ms
        return self._now_ms

    def format(self, t: SimTimeMs | None = None) -> str:
        """Render `t` (default: current time) as the wall-clock label
        offset from `start_wall_label`, formatted HH:MM:SS.mmm."""
        ms = self._now_ms if t is None else t
        if ms < 0:
            raise ValueError(f"cannot format negative sim time: {ms}")
        start_h, start_m, start_s = (int(p) for p in self.start_wall_label.split(":"))
        start_total_ms = ((start_h * 3600) + (start_m * 60) + start_s) * 1000
        total_ms = (start_total_ms + ms) % (24 * 3600 * 1000)
        hours, rem_ms = divmod(total_ms, 3_600_000)
        minutes, rem_ms = divmod(rem_ms, 60_000)
        seconds, millis = divmod(rem_ms, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


class RealtimePacer:
    """Optional helper to pace a sim loop against real wall-clock time for
    live demos (e.g. "1x speed" = 1 sim-second per real second). Never used
    by control-system logic, never a source of truth for `SimClock.now()`.
    """

    def __init__(self, speed_multiplier: float = 1.0) -> None:
        if speed_multiplier <= 0:
            raise ValueError("speed_multiplier must be positive")
        self.speed_multiplier = speed_multiplier
        self._last_real_s = _real_time.monotonic()

    def sim_ms_elapsed(self) -> int:
        """How many sim-ms have elapsed in real wall-clock time since the
        last call, scaled by the speed multiplier."""
        now_real_s = _real_time.monotonic()
        elapsed_real_s = now_real_s - self._last_real_s
        self._last_real_s = now_real_s
        return int(elapsed_real_s * self.speed_multiplier * 1000)
