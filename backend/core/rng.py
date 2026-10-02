"""Seeded, named randomness streams (ADR-002).

No module may import the global `random` module. Every consumer gets its own
`random.Random` instance from `RngFactory.stream(name)`, derived
deterministically from `(seed, name)` via SHA-256 — not Python's built-in
`hash()`, which is salted per-process and would break reproducibility across
runs/processes. Deriving by name (rather than by draw order) also means
adding a brand-new consumer never perturbs the sequence an existing consumer
already depends on.
"""

from __future__ import annotations

import hashlib
import random


class RngFactory:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._streams: dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        if name not in self._streams:
            self._streams[name] = random.Random(self._derive_seed(name))
        return self._streams[name]

    def _derive_seed(self, name: str) -> int:
        digest = hashlib.sha256(f"{self.seed}:{name}".encode()).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=False)
