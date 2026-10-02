"""Deterministic simulated time source.

All system code must read time through SimClock, never via time.time()/datetime.now().
This is what makes a run reproducible from its seed (PRD §8.7, AC-02).
"""

from __future__ import annotations

import random


class SimClock:
    """Monotonic simulated clock, advanced explicitly by the simulation loop.

    `now_s` is seconds since the sim epoch (sim start = 0.0).
    """

    def __init__(self, tick_hz: int = 10) -> None:
        if tick_hz <= 0:
            raise ValueError("tick_hz must be positive")
        self._tick_hz = tick_hz
        self._tick_dt = 1.0 / tick_hz
        self._ticks = 0

    @property
    def now_s(self) -> float:
        return self._ticks * self._tick_dt

    @property
    def tick_dt(self) -> float:
        return self._tick_dt

    @property
    def ticks(self) -> int:
        return self._ticks

    def advance(self, n_ticks: int = 1) -> float:
        if n_ticks < 0:
            raise ValueError("n_ticks must be non-negative")
        self._ticks += n_ticks
        return self.now_s

    def reset(self) -> None:
        self._ticks = 0

    def hms(self) -> str:
        total = int(self.now_s)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


class SeededRandom:
    """Wraps random.Random so every module draws from one traceable seed tree.

    Child streams are derived deterministically by name so that adding/removing
    a consumer doesn't perturb the sequences other consumers draw (PRD §8.7).
    """

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._root = random.Random(seed)
        self._children: dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        if name not in self._children:
            child_seed = self._root.randrange(2**32)
            self._children[name] = random.Random(child_seed ^ hash(name) & 0xFFFFFFFF)
        return self._children[name]
