"""Wraps `IntersectionStateMachine` as the unit the rest of the system (sim
network, the safety gate) actually talks to: ticking, reading what the
physical signal heads show, and handling messages — including, as of
Phase 3, PREEMPT/RENEW/RELEASE commands. This is the controller guard: the
SECOND, local safety layer (PRD §10.8 Part A2) that re-validates everything
the Safety Supervisor already approved, because the supervisor's view of
the world can be stale and this controller's cannot.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from controller.messages import Ping, Pong, Unsupported
from controller.state_machine import IntersectionStateMachine, Transition
from core.clock import SimTimeMs
from core.events import Event, EventIdFactory, EventType
from core.models import (
    ActiveHoldInfo,
    Command,
    CommandAck,
    CommandAckStatus,
    CommandType,
    ControllerStatus,
    Direction,
    Intersection,
    SignalState,
    StatusReport,
)
from core.reasons import ReasonCode
from core.security import verify

ControllerMessage = Ping | Command
ControllerReply = Pong | Unsupported | CommandAck


@dataclass
class ActiveHold:
    phase_id: str
    hold_until: SimTimeMs
    plan_version: int


class IntersectionController:
    def __init__(
        self,
        intersection: Intersection,
        event_ids: EventIdFactory | None = None,
        ped_walk_ms: int = 0,
        max_hold_ms: int = 90_000,
        hmac_secret: str = "dev-only-insecure-secret-do-not-use-in-production",
        idempotency_store_size: int = 1000,
    ) -> None:
        self.intersection_id = intersection.id
        self._intersection = intersection
        self._sm = IntersectionStateMachine(intersection, ped_walk_ms=ped_walk_ms)
        self._event_ids = event_ids or EventIdFactory()
        self.events: list[Event] = []

        self._max_hold_ms = max_hold_ms
        self._secret = hmac_secret
        self._applied_plan_version = 0
        self._active_hold: ActiveHold | None = None
        self._hold_interrupted_for: str | None = None

        self._idempotency_store_size = idempotency_store_size
        self._idempotency_cache: dict[str, CommandAck] = {}
        self._idempotency_order: deque[str] = deque()

        # Full (command, ack) history for every command this controller has
        # ever processed — read-only observability for the invariant
        # checker (INV-09/INV-10) and chaos tooling. Not used by the guard
        # itself, which relies only on the idempotency cache and
        # _applied_plan_version above.
        self.command_log: list[tuple[Command, CommandAck]] = []

    @property
    def applied_plan_version(self) -> int:
        return self._applied_plan_version

    # -- tick ------------------------------------------------------------

    def tick(self, now: SimTimeMs) -> list[Transition]:
        transitions = self._sm.tick(now)
        for t in transitions:
            self.events.append(
                Event(
                    id=self._event_ids.next_id(),
                    type=EventType.SIGNAL_CHANGED,
                    timestamp=t.at,
                    payload={
                        "intersection_id": self.intersection_id,
                        "from_state": t.from_state.value,
                        "to_state": t.to_state.value,
                        "phase_id": t.phase_id,
                    },
                )
            )
        self._enforce_max_hold(now)
        return transitions

    def build_status_report(self, sequence: int, now: SimTimeMs) -> StatusReport:
        sm = self._sm
        status = (
            ControllerStatus.FLASHING
            if sm.state == SignalState.FLASHING_RED
            else ControllerStatus.ONLINE
        )
        hold = self._active_hold
        return StatusReport(
            intersection_id=self.intersection_id,
            sequence=sequence,
            sent_at=now,
            controller_status=status,
            signal_state=sm.state,
            current_phase_id=sm.current_phase_id,
            state_started_at=sm.state_started_at,
            red_since=dict(sm.red_since),
            applied_plan_version=self._applied_plan_version,
            active_hold=(
                ActiveHoldInfo(phase_id=hold.phase_id, hold_until=hold.hold_until) if hold else None
            ),
        )

    def drain_events(self) -> list[Event]:
        events, self.events = self.events, []
        return events

    def signal_view(self) -> dict[Direction, SignalState]:
        """What the physical signal heads show right now, per approach."""
        green = self._sm.green_movements()
        amber = self._sm.amber_movements()
        view: dict[Direction, SignalState] = {}
        for approach in self._intersection.approaches:
            d = approach.direction
            if self._sm.state == SignalState.FLASHING_RED:
                view[d] = SignalState.FLASHING_RED
            elif d in green:
                view[d] = SignalState.GREEN
            elif d in amber:
                view[d] = SignalState.AMBER
            else:
                view[d] = SignalState.ALL_RED
        return view

    @property
    def state_machine(self) -> IntersectionStateMachine:
        return self._sm

    @property
    def active_hold(self) -> ActiveHold | None:
        return self._active_hold

    # -- message handling -------------------------------------------------

    def handle_message(self, message: object, now: SimTimeMs) -> ControllerReply:
        if isinstance(message, Ping):
            return Pong(
                message_id=message.message_id, intersection_id=self.intersection_id, replied_at=now
            )
        if isinstance(message, Command):
            return self._handle_command(message, now)
        message_id = getattr(message, "message_id", "unknown")
        return Unsupported(message_id=message_id, reason="unsupported message type")

    # -- controller guard (PRD §10.8 Part A2) ------------------------------

    def _handle_command(self, command: Command, now: SimTimeMs) -> CommandAck:
        ack = self._guard(command, now)
        self.command_log.append((command, ack))
        return ack

    def _guard(self, command: Command, now: SimTimeMs) -> CommandAck:
        # 1. Approval token.
        if not verify(command, command.approval, self._secret):
            return self._ack(command, now, CommandAckStatus.REJECTED, ReasonCode.NOT_APPROVED)

        # 2. Idempotency — a repeated key is never reprocessed, whatever the
        # first outcome was (INV-09). Bounded store: see docs/decisions.md.
        cached = self._idempotency_cache.get(command.idempotency_key)
        if cached is not None:
            updates = {"status": CommandAckStatus.DUPLICATE, "timestamp": now}
            return cached.model_copy(update=updates)

        # 3. Plan version monotonicity (INV-08).
        if command.plan_version < self._applied_plan_version:
            ack = self._ack(
                command,
                now,
                CommandAckStatus.IGNORED_STALE_VERSION,
                ReasonCode.STALE_VERSION,
                plan_version=command.plan_version,
                applied_version=self._applied_plan_version,
            )
            self._remember(command.idempotency_key, ack)
            return ack

        # 4. Local FLASHING_RED check (independent of the supervisor's view).
        if self._sm.state == SignalState.FLASHING_RED:
            ack = self._ack(command, now, CommandAckStatus.REJECTED, ReasonCode.FLASHING)
            self._remember(command.idempotency_key, ack)
            return ack

        ack = self._dispatch(command, now)
        self._remember(command.idempotency_key, ack)
        version_advances = command.plan_version > self._applied_plan_version
        if ack.status == CommandAckStatus.EXECUTED and version_advances:
            self._applied_plan_version = command.plan_version
        return ack

    def _dispatch(self, command: Command, now: SimTimeMs) -> CommandAck:
        if command.type == CommandType.PREEMPT:
            return self._handle_preempt(command, now)
        if command.type == CommandType.RENEW:
            return self._handle_renew(command, now)
        if command.type == CommandType.RELEASE:
            return self._handle_release(command, now)
        return self._ack(
            command,
            now,
            CommandAckStatus.REJECTED,
            ReasonCode.UNSUPPORTED,
            command_type=str(command.type),
        )

    def _handle_preempt(self, command: Command, now: SimTimeMs) -> CommandAck:
        phase_id = command.payload.get("phase_id")
        hold_until = command.payload.get("hold_until")

        # 5. Phase validity.
        if not isinstance(phase_id, str) or not self._sm.has_phase(phase_id):
            return self._ack(
                command,
                now,
                CommandAckStatus.REJECTED,
                ReasonCode.UNKNOWN_PHASE,
                phase_id=phase_id,
                intersection_id=self.intersection_id,
            )
        if not isinstance(hold_until, int) or hold_until <= now:
            return self._ack(
                command, now, CommandAckStatus.REJECTED, ReasonCode.MALFORMED, detail="hold_until"
            )

        # 6. Timing safety — may defer, never rejects for timing alone.
        result = self._sm.request_phase_change(phase_id, now)
        if not result.accepted:
            return self._ack(command, now, CommandAckStatus.REJECTED, ReasonCode.FLASHING)

        self._active_hold = ActiveHold(
            phase_id=phase_id, hold_until=hold_until, plan_version=command.plan_version
        )
        self._hold_interrupted_for = None
        return self._ack(
            command, now, CommandAckStatus.EXECUTED, None, effective_at=result.earliest_green_at
        )

    def _handle_renew(self, command: Command, now: SimTimeMs) -> CommandAck:
        hold_until = command.payload.get("hold_until")
        if self._active_hold is None:
            return self._ack(
                command,
                now,
                CommandAckStatus.REJECTED,
                ReasonCode.MALFORMED,
                detail="no active hold",
            )
        if not isinstance(hold_until, int) or hold_until <= now:
            return self._ack(
                command, now, CommandAckStatus.REJECTED, ReasonCode.MALFORMED, detail="hold_until"
            )
        self._active_hold.hold_until = hold_until
        return self._ack(command, now, CommandAckStatus.EXECUTED, None, effective_at=now)

    def _handle_release(self, command: Command, now: SimTimeMs) -> CommandAck:
        self._active_hold = None
        self._hold_interrupted_for = None
        self._sm.clear_pending_request()
        return self._ack(command, now, CommandAckStatus.EXECUTED, None, effective_at=now)

    # -- local max-hold enforcement (INV-06), independent of the supervisor --

    def _enforce_max_hold(self, now: SimTimeMs) -> None:
        if self._active_hold is None:
            return

        if now >= self._active_hold.hold_until:
            self._active_hold = None
            self._hold_interrupted_for = None
            self._sm.clear_pending_request()
            return

        if self._hold_interrupted_for is not None:
            serving_interrupt = (
                self._sm.current_phase_id == self._hold_interrupted_for
                and self._sm.state == SignalState.GREEN
            )
            if serving_interrupt:
                phase = self._sm.phase(self._hold_interrupted_for)
                if now >= self._sm.phase_started_at + phase.min_green_ms:
                    self._hold_interrupted_for = None
                    self._sm.request_phase_change(self._active_hold.phase_id, now)
            return

        starved = self._find_starved_direction(now)
        if starved is not None:
            interrupt_phase = self._phase_for_direction(starved)
            if interrupt_phase is not None and interrupt_phase != self._active_hold.phase_id:
                self._hold_interrupted_for = interrupt_phase
                self._sm.request_phase_change(interrupt_phase, now)

    def _find_starved_direction(self, now: SimTimeMs) -> Direction | None:
        for direction in sorted(self._sm.red_since):
            since = self._sm.red_since[direction]
            if now - since >= self._max_hold_ms:
                return direction
        return None

    def _phase_for_direction(self, direction: Direction) -> str | None:
        for phase in self._intersection.phases:
            if direction in phase.movements:
                return phase.id
        return None

    # -- helpers --------------------------------------------------------

    def _ack(
        self,
        command: Command,
        now: SimTimeMs,
        status: CommandAckStatus,
        reason_code: ReasonCode | None,
        effective_at: SimTimeMs | None = None,
        **_explain_kwargs: object,
    ) -> CommandAck:
        return CommandAck(
            command_id=command.id,
            intersection_id=self.intersection_id,
            idempotency_key=command.idempotency_key,
            status=status,
            reason_code=reason_code.value if reason_code else None,
            effective_at=effective_at,
            timestamp=now,
        )

    def _remember(self, key: str, ack: CommandAck) -> None:
        if key in self._idempotency_cache:
            return
        self._idempotency_cache[key] = ack
        self._idempotency_order.append(key)
        if len(self._idempotency_order) > self._idempotency_store_size:
            oldest = self._idempotency_order.popleft()
            self._idempotency_cache.pop(oldest, None)
