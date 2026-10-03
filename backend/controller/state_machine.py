"""The intersection controller's signal state machine (PRD §9.1, §7).

Field-side logic: this is what would run inside the roadside signal
cabinet. It is driven ONLY by `now: SimTimeMs` passed into each call — no
internal timers, no wall clock, no randomness. It knows nothing about the
network, the supervisor, or any other intersection.

Hard safety rule: no transition may shorten min green, amber, or all-red.
`request_phase_change` is the only way to ask for an early change, and it
always routes through AMBER -> ALL_RED like every other transition; it can
only ever be deferred, never granted early.

Migrated to the general junction model (PRD Phase 6 Stage 6.2, ADR-051):
phases are sets of MOVEMENT ids (`JunctionPhase.movement_ids`), not compass
directions, and red time is tracked per APPROACH (an approach is "green"
when any of its movements is in the current phase's movement set) — this
is what the spec calls for ("red_since tracked per APPROACH") and what
INV-06 (per-approach max hold) needs. The conflict check runs over
`JunctionConflictMatrix`, derived from geometry (ADR-046), not an authored
compass-direction table.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.clock import SimTimeMs
from core.conflicts import ConflictRules
from core.junctions import Junction, JunctionConflictMatrix, JunctionPhase
from core.models import SignalState


class ControllerError(Exception):
    """Raised for programming errors (e.g. unknown phase id at construction)."""


@dataclass(frozen=True)
class PhaseChangeResult:
    accepted: bool
    earliest_green_at: SimTimeMs | None
    reason: str | None = None


@dataclass(frozen=True)
class Transition:
    """One state transition, for observers (events, invariant checks)."""

    at: SimTimeMs
    from_state: SignalState
    to_state: SignalState
    phase_id: str | None


class IntersectionStateMachine:
    """Pure, deterministic signal state machine for one intersection."""

    def __init__(self, intersection: Junction, ped_walk_ms: int = 0) -> None:
        if not intersection.phases:
            raise ControllerError(f"intersection {intersection.id} has no phases")

        self.intersection_id = intersection.id
        self._phases: dict[str, JunctionPhase] = {p.id: p for p in intersection.phases}
        # The order phases were generated in is the round-robin cycle order
        # (the general model has no separate default_plan; a phase's own
        # configured_green_ms is its share of that cycle).
        self._cycle: list[str] = [p.id for p in intersection.phases]
        self._configured_green_ms: dict[str, int] = {
            p.id: p.configured_green_ms for p in intersection.phases
        }
        self._conflict_matrix: JunctionConflictMatrix = intersection.conflict_matrix(
            _default_rules()
        )

        # movement id -> approach id, and phase id -> the set of approach ids
        # it serves (green), derived once from the junction's own geometry.
        self._approach_of_movement: dict[str, str] = {
            m.id: m.approach_id for m in intersection.movements
        }
        self._approaches_served: dict[str, frozenset[str]] = {
            phase.id: frozenset(
                self._approach_of_movement[mid]
                for mid in phase.movement_ids
                if mid in self._approach_of_movement
            )
            for phase in intersection.phases
        }
        self._all_approach_ids: list[str] = sorted(a.id for a in intersection.approaches)

        first_phase_id = self._cycle[0]
        self.state: SignalState = SignalState.GREEN
        self.current_phase_id: str | None = first_phase_id
        self.next_phase_id: str | None = None
        self.state_started_at: SimTimeMs = 0
        self.phase_started_at: SimTimeMs = 0
        # Index into self._cycle of the phase currently green/being approached.
        self._cycle_index = 0
        # Set by request_phase_change(); overrides the next phase in the
        # round-robin cycle once the current mandatory green is served.
        self._pending_target_phase_id: str | None = None
        self.flashing_reason: str | None = None

        # Pedestrian service (PRD §10.8 A4): a call for a phase that's not
        # currently green waits as "pending"; once that phase goes green,
        # service (WALK + clearance) starts and the green may not end
        # before it finishes, overriding even an explicit phase-change
        # request (clearance must never be cut short).
        self._ped_walk_ms = ped_walk_ms
        self._ped_call_pending: dict[str, bool] = dict.fromkeys(self._phases, False)
        self.ped_service_end_at: SimTimeMs | None = None

        # Per-approach: when it most recently became red (None if currently
        # served/green for that approach). Used for max-hold enforcement.
        self.red_since: dict[str, SimTimeMs] = {}
        self._init_red_since(at=0)

        self.transitions: list[Transition] = []

    # -- introspection -----------------------------------------------------

    def current_phase(self) -> JunctionPhase:
        assert self.current_phase_id is not None
        return self._phases[self.current_phase_id]

    def phase(self, phase_id: str) -> JunctionPhase:
        if phase_id not in self._phases:
            raise ControllerError(f"unknown phase '{phase_id}'")
        return self._phases[phase_id]

    def has_phase(self, phase_id: str) -> bool:
        return phase_id in self._phases

    def green_movements(self) -> set[str]:
        if self.state == SignalState.GREEN and self.current_phase_id is not None:
            return set(self.current_phase().movement_ids)
        return set()

    def amber_movements(self) -> set[str]:
        if self.state == SignalState.AMBER and self.current_phase_id is not None:
            return set(self.current_phase().movement_ids)
        return set()

    def served_approaches(self, phase_id: str) -> frozenset[str]:
        """Approach ids this phase serves (i.e. shows green to)."""
        return self._approaches_served[phase_id]

    # -- core update ---------------------------------------------------

    def tick(self, now: SimTimeMs) -> list[Transition]:
        """Advance the state machine to `now`. May produce zero or more
        transitions (a single tick can cross multiple boundaries only if
        called with a large `now` jump; each boundary is still a distinct,
        correctly-timed Transition record)."""
        if self.state == SignalState.FLASHING_RED:
            self._check_invariant(now)
            return []

        produced: list[Transition] = []
        # Loop: a single tick() call may need to process several
        # transitions if `now` has jumped far (e.g. AMBER then ALL_RED both
        # elapsed since the last tick). Each step only ever advances to the
        # next boundary, never skips one.
        while True:
            next_transition = self._maybe_advance(now)
            if next_transition is None:
                break
            produced.append(next_transition)

        self._check_invariant(now)
        return produced

    def _maybe_advance(self, now: SimTimeMs) -> Transition | None:
        if self.state == SignalState.GREEN:
            target = self._next_target_phase_id()
            ready_at = self._green_ready_at(self.current_phase_id)  # type: ignore[arg-type]
            if now < ready_at:
                return None
            if target == self.current_phase_id:
                # Re-requested the same phase: nothing to do, stay green.
                return None
            return self._enter_amber(now, target)

        if self.state == SignalState.AMBER:
            phase = self.current_phase()
            amber_ends_at = self.state_started_at + phase.amber_ms
            if now < amber_ends_at:
                return None
            return self._enter_all_red(now)

        if self.state == SignalState.ALL_RED:
            next_target = self.next_phase_id
            assert next_target is not None
            target_phase = self.phase(next_target)
            all_red_ends_at = self.state_started_at + target_phase.all_red_ms
            if now < all_red_ends_at:
                return None
            return self._enter_green(now, next_target)

        return None

    def _next_target_phase_id(self) -> str:
        if self._pending_target_phase_id is not None:
            return self._pending_target_phase_id
        return self._cycle[(self._cycle_index + 1) % len(self._cycle)]

    def _green_ready_at(self, phase_id: str) -> SimTimeMs:
        """Earliest time GREEN may end for `phase_id`. Normal cycling holds
        the full configured_green_ms share; an explicit
        request_phase_change() only needs min_green_ms (the safety floor)
        to elapse — that is what lets a later preemption shorten (never
        below the floor) an otherwise longer scheduled green. An active
        pedestrian service floor (if any) applies unconditionally, even
        over a pending request — clearance is never cut short."""
        phase = self.phase(phase_id)
        floor = self.phase_started_at + phase.min_green_ms
        if self.ped_service_end_at is not None:
            floor = max(floor, self.ped_service_end_at)
        if self._pending_target_phase_id is not None:
            return floor
        configured_met_at = self.phase_started_at + self._configured_green_ms[phase_id]
        return max(floor, configured_met_at)

    def _enter_amber(self, now: SimTimeMs, target_phase_id: str) -> Transition:
        from_state = self.state
        self.next_phase_id = target_phase_id
        self.state = SignalState.AMBER
        self.state_started_at = now
        t = Transition(now, from_state, self.state, self.current_phase_id)
        self.transitions.append(t)
        return t

    def _enter_all_red(self, now: SimTimeMs) -> Transition:
        from_state = self.state
        ending_phase = self.current_phase()
        self._mark_red(now, self.served_approaches(ending_phase.id))
        self.current_phase_id = None
        self.state = SignalState.ALL_RED
        self.state_started_at = now
        t = Transition(now, from_state, self.state, None)
        self.transitions.append(t)
        return t

    def _enter_green(self, now: SimTimeMs, phase_id: str) -> Transition:
        from_state = self.state
        new_phase = self.phase(phase_id)
        self._mark_green(self.served_approaches(phase_id))
        self.current_phase_id = phase_id
        self.state = SignalState.GREEN
        self.state_started_at = now
        self.phase_started_at = now
        if phase_id in self._cycle:
            self._cycle_index = self._cycle.index(phase_id)
        if self._pending_target_phase_id == phase_id:
            self._pending_target_phase_id = None
        self.next_phase_id = None

        if self._ped_call_pending.get(phase_id):
            self._ped_call_pending[phase_id] = False
            self.ped_service_end_at = now + self._ped_walk_ms + new_phase.ped_clearance_ms
        else:
            self.ped_service_end_at = None

        t = Transition(now, from_state, self.state, phase_id)
        self.transitions.append(t)
        return t

    # -- public requests -------------------------------------------------

    def request_phase_change(self, target_phase_id: str, now: SimTimeMs) -> PhaseChangeResult:
        """Safely request a change to `target_phase_id`. Never bypasses
        timing rules — only ever defers. This is also how a hold is
        maintained: requesting the already-current phase still sets it as
        the pending target, which is what stops the state machine from
        rotating away once the phase's normal configured_green_ms elapses
        (see `_green_ready_at`) — a hold is "requesting to stay"."""
        if target_phase_id not in self._phases:
            return PhaseChangeResult(accepted=False, earliest_green_at=None, reason="unknown_phase")
        if self.state == SignalState.FLASHING_RED:
            return PhaseChangeResult(accepted=False, earliest_green_at=None, reason="flashing")

        self._pending_target_phase_id = target_phase_id
        if self.current_phase_id == target_phase_id and self.state == SignalState.GREEN:
            return PhaseChangeResult(accepted=True, earliest_green_at=self.phase_started_at)

        earliest = self._earliest_green_at(target_phase_id, now)
        return PhaseChangeResult(accepted=True, earliest_green_at=earliest)

    def clear_pending_request(self) -> None:
        """Releases any pending target set by request_phase_change(),
        resuming normal round-robin cycling from the current phase forward
        (ADR-010). Used when a hold is released or expires."""
        self._pending_target_phase_id = None

    def _earliest_green_at(self, target_phase_id: str, now: SimTimeMs) -> SimTimeMs:
        target_phase = self.phase(target_phase_id)
        if self.state == SignalState.GREEN:
            phase = self.current_phase()
            floor = self.phase_started_at + phase.min_green_ms
            if self.ped_service_end_at is not None:
                floor = max(floor, self.ped_service_end_at)
            amber_start = max(now, floor)
            all_red_start = amber_start + phase.amber_ms
            return all_red_start + target_phase.all_red_ms
        if self.state == SignalState.AMBER:
            phase = self.current_phase()
            amber_ends_at = self.state_started_at + phase.amber_ms
            return amber_ends_at + target_phase.all_red_ms
        if self.state == SignalState.ALL_RED:
            all_red_ends_at = self.state_started_at + target_phase.all_red_ms
            return all_red_ends_at
        return now

    def register_pedestrian_call(self, phase_id: str, now: SimTimeMs) -> None:
        """A pedestrian call for `phase_id`'s crossing. If that phase is
        already green, starts its service window immediately (if not
        already running); otherwise marks it pending so service starts the
        next time that phase goes green."""
        if phase_id not in self._phases:
            raise ControllerError(f"unknown phase '{phase_id}'")
        if self.state == SignalState.FLASHING_RED:
            return
        if self.state == SignalState.GREEN and self.current_phase_id == phase_id:
            if self.ped_service_end_at is None:
                phase = self.phase(phase_id)
                self.ped_service_end_at = now + self._ped_walk_ms + phase.ped_clearance_ms
            return
        self._ped_call_pending[phase_id] = True

    def enter_flashing(self, reason: str, now: SimTimeMs) -> None:
        """Fail-safe: enter FLASHING_RED. Terminal for this phase of the
        project — recovery is an explicit operator/health action in a
        later phase, never automatic (see ADR on controller recovery)."""
        if self.state == SignalState.FLASHING_RED:
            return
        from_state = self.state
        self._mark_all_red(now)
        self.current_phase_id = None
        self.next_phase_id = None
        self.state = SignalState.FLASHING_RED
        self.state_started_at = now
        self.flashing_reason = reason
        self.transitions.append(Transition(now, from_state, self.state, None))

    # -- invariant self-check (local last line of defence) ----------------

    def _check_invariant(self, now: SimTimeMs) -> None:
        active = self.green_movements() | self.amber_movements()
        reason = detect_conflict(self._conflict_matrix, active)
        if reason is not None:
            self.enter_flashing(reason, now)

    # -- red-time bookkeeping (feeds max-hold enforcement later) ----------

    def _init_red_since(self, at: SimTimeMs) -> None:
        green_now = self.served_approaches(self.current_phase_id) if self.current_phase_id else frozenset()
        for approach_id in self._all_approach_ids:
            if approach_id not in green_now:
                self.red_since[approach_id] = at

    def _mark_red(self, now: SimTimeMs, approach_ids: frozenset[str]) -> None:
        for a in approach_ids:
            if a not in self.red_since:
                self.red_since[a] = now

    def _mark_green(self, approach_ids: frozenset[str]) -> None:
        for a in approach_ids:
            self.red_since.pop(a, None)

    def _mark_all_red(self, now: SimTimeMs) -> None:
        served = self.served_approaches(self.current_phase_id) if self.current_phase_id else frozenset()
        for a in served:
            self.red_since.setdefault(a, now)


def _default_rules() -> ConflictRules:
    return ConflictRules()


def detect_conflict(conflict_matrix: JunctionConflictMatrix, movement_ids: set[str]) -> str | None:
    """Pure function: returns a reason string if any two movements in
    `movement_ids` conflict, else None. Kept standalone (not a method) so it
    can be unit-tested directly against hand-built inputs without having to
    drive the state machine through its public API into a broken state."""
    items = sorted(movement_ids)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if conflict_matrix.conflicts(a, b):
                return f"conflicting movements green simultaneously: {a} and {b}"
    return None
