"""Wraps `IntersectionStateMachine` as the unit the rest of the system (sim
network, the safety gate) actually talks to: ticking, reading what the
physical signal heads show, and handling messages — including, as of
Phase 3, PREEMPT/RENEW/RELEASE commands. This is the controller guard: the
SECOND, local safety layer (PRD §10.8 Part A2) that re-validates everything
the Safety Supervisor already approved, because the supervisor's view of
the world can be stale and this controller's cannot.

Migrated to the general junction model (PRD Phase 6 Stage 6.2, ADR-051):
reads/reports movement ids and approach ids rather than compass directions.
`movement_view()` is the precise signal state per movement — the one that
matters for correctness, since ADR-050 found that two movements from the
same approach can legitimately be in different phases (a far-side turn
split from the through/near-side phase). `approach_view()` is a coarser
per-approach summary (green if ANY of its movements currently show green)
for callers that only need to know whether an approach has any service at
all, not which specific turn.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from controller.messages import Ping, Pong, Unsupported
from controller.state_machine import IntersectionStateMachine, Transition
from core.clock import SimTimeMs
from core.events import Event, EventIdFactory, EventType
from core.junctions import Junction
from core.models import (
    ActiveHoldInfo,
    Command,
    CommandAck,
    CommandAckStatus,
    CommandType,
    ControllerStatus,
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
        intersection: Junction,
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

    def movement_view(self) -> dict[str, SignalState]:
        """What each movement currently shows — the precise view, since two
        movements from the same approach can be in different phases."""
        green = self._sm.green_movements()
        amber = self._sm.amber_movements()
        view: dict[str, SignalState] = {}
        for movement in self._intersection.movements:
            if self._sm.state == SignalState.FLASHING_RED:
                view[movement.id] = SignalState.FLASHING_RED
            elif movement.id in green:
                view[movement.id] = SignalState.GREEN
            elif movement.id in amber:
                view[movement.id] = SignalState.AMBER
            else:
                view[movement.id] = SignalState.ALL_RED
        return view

    def approach_view(self) -> dict[str, SignalState]:
        """Per approach: GREEN if any of its movements show GREEN, else
        AMBER if any show AMBER, else ALL_RED (or FLASHING_RED). A coarser
        summary for callers that only need to know an approach has some
        service, not which specific turn."""
        movements = self.movement_view()
        approach_of = {m.id: m.approach_id for m in self._intersection.movements}
        view: dict[str, SignalState] = {}
        for approach in self._intersection.approaches:
            states = {
                movements[mid]
                for mid, aid in approach_of.items()
                if aid == approach.id and mid in movements
            }
            if SignalState.FLASHING_RED in states:
                view[approach.id] = SignalState.FLASHING_RED
            elif SignalState.GREEN in states:
                view[approach.id] = SignalState.GREEN
            elif SignalState.AMBER in states:
                view[approach.id] = SignalState.AMBER
            else:
                view[approach.id] = SignalState.ALL_RED
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
    #
    # Runs on EVERY tick, whether or not a command hold is active (ADR-053).
    # With only two phases (every signalised node in the old N/S/E/W model),
    # resuming normal round-robin rotation after a hold always serves the
    # starved side within one transition — there was only ever one "other"
    # group. With more phases, that is no longer true: rotation resumes from
    # wherever it was interrupted, and several phases can stand between that
    # point and the one a starved approach needs, even after the hold itself
    # has expired with no command ever rejected or ignored. So the rescue
    # must be unconditional, not merely "while a hold is active".
    #
    # Two more things are needed for a hold to stay safe when several
    # approaches starve at once:
    #   - after rescuing one, check again for MORE starvation before
    #     resuming the hold (or returning to normal cycling), instead of
    #     always stopping after exactly one rescue;
    #   - pick the rescue phase covering the MOST currently-starved
    #     approaches at once, since one phase can serve several together
    #     (e.g. a paired through+near-side phase).
    # Starvation is checked with a lead margin (one worst-case transition
    # ahead of the hard limit), because detecting it only once the limit is
    # already reached leaves no time to actually switch the signal before
    # the limit is exceeded.

    def _enforce_max_hold(self, now: SimTimeMs) -> None:
        if self._active_hold is not None and now >= self._active_hold.hold_until:
            self._active_hold = None
            self._hold_interrupted_for = None
            self._sm.clear_pending_request()
            # Fall through: still check starvation this same tick.

        if self._hold_interrupted_for is not None:
            serving_interrupt = (
                self._sm.current_phase_id == self._hold_interrupted_for
                and self._sm.state == SignalState.GREEN
            )
            if not serving_interrupt:
                return  # still transitioning toward the interrupt phase
            phase = self._sm.phase(self._hold_interrupted_for)
            if now < self._sm.phase_started_at + phase.min_green_ms:
                return  # interrupt floor not yet served
            self._hold_interrupted_for = None
            # Fall through: check for further starvation before resuming.

        starved = self._find_starved_approaches(now)
        if starved:
            exclude = {self._active_hold.phase_id} if self._active_hold is not None else set()
            interrupt_phase = self._best_interrupt_phase(starved, exclude)
            if interrupt_phase is not None:
                self._hold_interrupted_for = interrupt_phase
                self._sm.request_phase_change(interrupt_phase, now)
                return

        # No (more) starvation to rescue: resume the command hold if one is
        # active, otherwise release any leftover pending target so normal
        # round-robin cycling continues on its own.
        if self._active_hold is not None:
            self._sm.request_phase_change(self._active_hold.phase_id, now)
        else:
            self._sm.clear_pending_request()

    def _rescue_lead_ms(self) -> int:
        return max(
            (p.amber_ms + p.all_red_ms + p.min_green_ms for p in self._intersection.phases),
            default=0,
        )

    def _find_starved_approaches(self, now: SimTimeMs) -> set[str]:
        trigger_at = self._max_hold_ms - self._rescue_lead_ms()
        return {
            approach_id
            for approach_id, since in self._sm.red_since.items()
            if now - since >= trigger_at
        }

    def _best_interrupt_phase(self, starved: set[str], exclude: set[str]) -> str | None:
        """The phase (other than any excluded one) covering the most
        starved approaches at once, so one rescue can resolve several at a
        time. Ties go to the lowest phase id (sorted iteration, strict >
        update), for determinism."""
        best_id: str | None = None
        best_count = 0
        for phase in sorted(self._intersection.phases, key=lambda p: p.id):
            if phase.id in exclude:
                continue
            covered = len(self._sm.served_approaches(phase.id) & starved)
            if covered > best_count:
                best_id, best_count = phase.id, covered
        return best_id

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
