"""The Safety Supervisor — the CENTRAL policy layer of the two-layer safety
architecture (PRD §10.8 Part B; see docs/decisions.md for the ADR on the
split with the controller guard).

It knows policy, city config, and the last REPORTED state of each
intersection (`IntersectionView`) — which may be stale. It deliberately
does NOT try to enforce exact timing (amber/all-red/min-green) itself,
because its view could be out of date by the time a command arrives; that
is the controller guard's job, which always has the true, current state.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from core.clock import Clock, SimTimeMs
from core.config import Settings
from core.events import Event, EventIdFactory, EventType
from core.models import City, Command, CommandType, ControllerStatus, Direction
from core.reasons import ReasonCode, explain
from core.security import sign

_IDEMPOTENCY_KEY_RE = re.compile(r"^v\d+:[^:]+:\d{4}$")


@dataclass(frozen=True)
class IntersectionView:
    """The supervisor's last known picture of one intersection — may be
    stale; `reported_at` says how stale."""

    intersection_id: str
    controller_status: ControllerStatus
    reported_at: SimTimeMs
    current_phase_id: str | None
    red_since: dict[Direction, SimTimeMs] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason_code: ReasonCode | None
    explanation: str
    approval: str | None = None
    view_stale: bool = False


class SafetySupervisor:
    def __init__(
        self, city: City, settings: Settings, clock: Clock, secret: str | None = None
    ) -> None:
        self.city = city
        self.settings = settings
        self.clock = clock
        self._secret = secret if secret is not None else settings.hmac_secret

        # Precomputed lookups for <5ms validation (Part B4).
        self._phase_movements: dict[tuple[str, str], frozenset[Direction]] = {}
        self._intersection_ids = {n.id for n in city.intersections}
        for node in city.intersections:
            for phase in node.phases:
                self._phase_movements[(node.id, phase.id)] = frozenset(phase.movements)
        self._conflict_matrices = {n.id: n.conflict_matrix for n in city.intersections}

        self._plan_versions: dict[str, int] = {}
        self._rate_window: dict[str, deque[SimTimeMs]] = {}

        self._crashed = False
        self._last_heartbeat_at: SimTimeMs = clock.now()

        self._event_ids = EventIdFactory()
        self.events: list[Event] = []

    # -- liveness (FH-05, chaos testing) -----------------------------------

    def heartbeat(self) -> None:
        self._last_heartbeat_at = self.clock.now()

    def crash(self) -> None:
        self._crashed = True

    def restart(self) -> None:
        self._crashed = False
        self._last_heartbeat_at = self.clock.now()

    def is_healthy(self) -> bool:
        if self._crashed:
            return False
        age = self.clock.now() - self._last_heartbeat_at
        return age <= self.settings.heartbeat_timeout_ms

    # -- validation -----------------------------------------------------

    def validate(self, command: Command, view: IntersectionView) -> Decision:
        now = self.clock.now()

        decision = self._check_wellformed(command, now)
        if decision is not None:
            return self._finish(command, decision)

        decision = self._check_target_and_phase(command)
        if decision is not None:
            return self._finish(command, decision)

        decision = self._check_controllable(command, view)
        if decision is not None:
            return self._finish(command, decision)

        decision = self._check_version(command)
        if decision is not None:
            return self._finish(command, decision)

        decision = self._check_hold_policy(command, view, now)
        if decision is not None:
            return self._finish(command, decision)

        decision = self._check_rate_limit(command, now)
        if decision is not None:
            return self._finish(command, decision)

        view_stale = (now - view.reported_at) > self.settings.heartbeat_timeout_ms
        approval = sign(command, self._secret)
        self._plan_versions[command.intersection_id] = max(
            self._plan_versions.get(command.intersection_id, 0), command.plan_version
        )
        self._record_rate(command.intersection_id, now)
        explanation = "Approved"
        if view_stale:
            explanation += " (view is stale; controller timing is authoritative)"
        decision = Decision(
            approved=True,
            reason_code=None,
            explanation=explanation,
            approval=approval,
            view_stale=view_stale,
        )
        return self._finish(command, decision)

    # -- individual checks, each returning a Decision only on rejection ---

    def _check_wellformed(self, command: Command, now: SimTimeMs) -> Decision | None:
        if not _IDEMPOTENCY_KEY_RE.match(command.idempotency_key):
            detail = f"bad idempotency_key format: {command.idempotency_key}"
            return self._reject(ReasonCode.MALFORMED, detail=detail)
        if command.type in (CommandType.PREEMPT, CommandType.RENEW):
            hold_until = command.payload.get("hold_until")
            if not isinstance(hold_until, int) or hold_until <= now:
                return self._reject(ReasonCode.MALFORMED, detail="hold_until must be in the future")
        if command.type == CommandType.PREEMPT:
            phase_id = command.payload.get("phase_id")
            if not isinstance(phase_id, str) or not phase_id:
                return self._reject(ReasonCode.MALFORMED, detail="phase_id is required for PREEMPT")
        return None

    def _check_target_and_phase(self, command: Command) -> Decision | None:
        if command.intersection_id not in self._intersection_ids:
            return self._reject(ReasonCode.UNKNOWN_TARGET, intersection_id=command.intersection_id)
        if command.type == CommandType.PREEMPT:
            phase_id = command.payload.get("phase_id")
            assert isinstance(phase_id, str)  # already validated in _check_wellformed
            key = (command.intersection_id, phase_id)
            if key not in self._phase_movements:
                return self._reject(
                    ReasonCode.UNKNOWN_PHASE,
                    phase_id=phase_id,
                    intersection_id=command.intersection_id,
                )
            matrix = self._conflict_matrices[command.intersection_id]
            movements = self._phase_movements[key]
            if not matrix.is_compatible(set(movements)):
                return self._reject(ReasonCode.CONFLICTING_PHASE, phase_id=phase_id)
        return None

    def _check_controllable(self, command: Command, view: IntersectionView) -> Decision | None:
        if view.controller_status in (ControllerStatus.UNREACHABLE, ControllerStatus.FLASHING):
            return self._reject(
                ReasonCode.UNCONTROLLABLE,
                intersection_id=command.intersection_id,
                status=view.controller_status.value,
            )
        return None

    def _check_version(self, command: Command) -> Decision | None:
        applied = self._plan_versions.get(command.intersection_id, 0)
        if command.plan_version < applied:
            return self._reject(
                ReasonCode.STALE_VERSION, plan_version=command.plan_version, applied_version=applied
            )
        return None

    def _check_hold_policy(
        self, command: Command, view: IntersectionView, now: SimTimeMs
    ) -> Decision | None:
        if command.type not in (CommandType.PREEMPT, CommandType.RENEW):
            return None
        hold_until = command.payload["hold_until"]
        assert isinstance(hold_until, int)  # already validated in _check_wellformed
        requested_s = (hold_until - now) / 1000.0
        if requested_s > self.settings.lease_duration_s:
            return self._reject(
                ReasonCode.HOLD_TOO_LONG,
                requested_s=round(requested_s, 1),
                limit_s=self.settings.lease_duration_s,
            )

        if command.type != CommandType.PREEMPT:
            return None
        phase_id = command.payload["phase_id"]
        assert isinstance(phase_id, str)
        held_movements = self._phase_movements[(command.intersection_id, phase_id)]
        view_age_ms = max(0, now - view.reported_at)
        for direction, red_since in view.red_since.items():
            if direction in held_movements:
                continue  # expected to be red — that's the point of the hold
            projected_red_ms = (hold_until - red_since) + view_age_ms
            if projected_red_ms > self.settings.max_hold_ms:
                return self._reject(
                    ReasonCode.MAX_HOLD_EXCEEDED, limit_s=self.settings.max_hold_s
                )
        return None

    def _check_rate_limit(self, command: Command, now: SimTimeMs) -> Decision | None:
        window = self._rate_window.setdefault(command.intersection_id, deque())
        cutoff = now - 1000
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.settings.command_rate_limit_per_s:
            return self._reject(
                ReasonCode.RATE_LIMITED,
                intersection_id=command.intersection_id,
                limit=self.settings.command_rate_limit_per_s,
            )
        return None

    def _record_rate(self, intersection_id: str, now: SimTimeMs) -> None:
        self._rate_window.setdefault(intersection_id, deque()).append(now)

    # -- helpers --------------------------------------------------------

    def _reject(self, reason_code: ReasonCode, **kwargs: object) -> Decision:
        explanation = explain(reason_code, **kwargs)
        return Decision(approved=False, reason_code=reason_code, explanation=explanation)

    def _finish(self, command: Command, decision: Decision) -> Decision:
        event_type = EventType.COMMAND_APPROVED if decision.approved else EventType.COMMAND_REJECTED
        self.events.append(
            Event(
                id=self._event_ids.next_id(),
                type=event_type,
                timestamp=self.clock.now(),
                payload={
                    "command_id": command.id,
                    "intersection_id": command.intersection_id,
                    "idempotency_key": command.idempotency_key,
                    "reason_code": decision.reason_code.value if decision.reason_code else None,
                    "explanation": decision.explanation,
                    "view_stale": decision.view_stale,
                },
            )
        )
        return decision

    def drain_events(self) -> list[Event]:
        events, self.events = self.events, []
        return events
