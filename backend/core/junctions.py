"""General junction model (PRD Phase 6 Stage 6.1.1, ADR-046, ADR-047).

Expand-migrate-contract: this module is NEW and sits beside the existing
`core.models` classes, which stay in use until Stage 6.2. It describes any
junction shape: an approach is where a vehicle enters, an exit is where it
leaves, and a movement is one approach-to-exit pair. Conflicts are between
MOVEMENTS, not compass directions.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.clock import SimTimeMs
from core.conflicts import ArmGeometry, ConflictRules, MovementGeometry, conflict_pairs
from core.models import IntersectionKind, TurnType


class Approach(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    intersection_id: str
    segment_id: str
    bearing_deg: float = Field(ge=0, lt=360)
    lanes: int = Field(ge=1)


class Exit(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    intersection_id: str
    segment_id: str
    bearing_deg: float = Field(ge=0, lt=360)


class Movement(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    intersection_id: str
    approach_id: str
    exit_id: str
    turn_type: TurnType


class JunctionPhase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    intersection_id: str
    movement_ids: tuple[str, ...]
    min_green_ms: SimTimeMs = Field(gt=0)
    amber_ms: SimTimeMs = Field(gt=0)
    all_red_ms: SimTimeMs = Field(gt=0)
    ped_clearance_ms: SimTimeMs = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _movements_sorted_and_unique(self) -> JunctionPhase:
        if len(set(self.movement_ids)) != len(self.movement_ids):
            raise ValueError(f"phase {self.id} lists a movement twice")
        if list(self.movement_ids) != sorted(self.movement_ids):
            raise ValueError(f"phase {self.id} movement ids must be sorted")
        return self


class JunctionConflictMatrix(BaseModel):
    """Symmetric, irreflexive conflicts over movement ids.

    Stored as sorted (a, b) pairs with a < b, so equality of matrices is a
    structural comparison and the serialised form is deterministic."""

    model_config = ConfigDict(frozen=True)

    movement_ids: tuple[str, ...]
    pairs: tuple[tuple[str, str], ...]

    @model_validator(mode="after")
    def _well_formed(self) -> JunctionConflictMatrix:
        known = set(self.movement_ids)
        for a, b in self.pairs:
            if a == b:
                raise ValueError(f"movement {a} conflicts with itself")
            if a not in known or b not in known:
                raise ValueError(f"conflict pair ({a}, {b}) references an unknown movement")
            if a > b:
                raise ValueError(f"conflict pair ({a}, {b}) must be sorted")
        if list(self.pairs) != sorted(self.pairs):
            raise ValueError("conflict pairs must be sorted")
        return self

    def conflicts(self, a: str, b: str) -> bool:
        if a == b:
            return False
        key = (a, b) if a < b else (b, a)
        return key in self._pair_set()

    def _pair_set(self) -> frozenset[tuple[str, str]]:
        return frozenset(self.pairs)

    def is_compatible(self, movement_ids: Iterable[str]) -> bool:
        ids = sorted(set(movement_ids))
        return all(not self.conflicts(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :])


class Junction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: IntersectionKind
    x: float
    y: float
    approaches: tuple[Approach, ...]
    exits: tuple[Exit, ...]
    movements: tuple[Movement, ...]
    phases: tuple[JunctionPhase, ...] = ()

    def conflict_matrix(self, rules: ConflictRules) -> JunctionConflictMatrix:
        arms: dict[str, ArmGeometry] = {}
        for approach in self.approaches:
            arms[approach.id] = ArmGeometry(id=approach.id, bearing_deg=approach.bearing_deg)
        for exit_ in self.exits:
            arms[exit_.id] = ArmGeometry(id=exit_.id, bearing_deg=exit_.bearing_deg)
        geometry = [
            MovementGeometry(
                id=m.id, approach_id=m.approach_id, exit_id=m.exit_id, turn_type=m.turn_type
            )
            for m in self.movements
        ]
        ids = tuple(sorted(m.id for m in self.movements))
        return JunctionConflictMatrix(
            movement_ids=ids,
            pairs=tuple(sorted(conflict_pairs(geometry, arms, rules))),
        )
