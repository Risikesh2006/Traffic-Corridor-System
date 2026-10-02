"""The fail-closed gate — the ONLY path central code may use to send
commands to intersection controllers (PRD §10.8 Part B3, FR-SAF-04, FH-05).

`SafeSignalLink` depends only on the `SignalLink` Protocol from `core/`,
never on a concrete implementation — it is handed one by whoever composes
the system (a future `runtime/` composition root), so this module itself
never imports `sim`, same as every other central module.
"""

from __future__ import annotations

from collections.abc import Callable

from core.clock import Clock
from core.events import Event, EventIdFactory, EventType
from core.interfaces import SignalLink
from core.models import Command, CommandAck, ControllerStatus
from core.reasons import ReasonCode, explain
from safety.supervisor import Decision, IntersectionView, SafetySupervisor

ViewSource = Callable[[str], IntersectionView]


class SafeSignalLink:
    def __init__(
        self,
        link: SignalLink,
        supervisor: SafetySupervisor,
        view_source: ViewSource,
        clock: Clock,
    ) -> None:
        self._link = link
        self._supervisor = supervisor
        self._view_source = view_source
        self._clock = clock
        self._event_ids = EventIdFactory()
        self.events: list[Event] = []

    async def send(self, command: Command) -> Decision:
        if not self._supervisor.is_healthy():
            decision = Decision(
                approved=False,
                reason_code=ReasonCode.SUPERVISOR_UNAVAILABLE,
                explanation=explain(ReasonCode.SUPERVISOR_UNAVAILABLE),
            )
            self._log(command, decision)
            return decision

        view = self._view_source(command.intersection_id)
        decision = self._supervisor.validate(command, view)
        if decision.approved:
            signed = command.model_copy(update={"approval": decision.approval})
            await self._link.send(signed)
        self._log(command, decision)
        return decision

    def drain_acks(self) -> list[CommandAck]:
        return self._link.drain_acks()

    def controller_status(self, intersection_id: str) -> ControllerStatus:
        return self._link.controller_status(intersection_id)

    def drain_events(self) -> list[Event]:
        events, self.events = self.events, []
        return events

    def _log(self, command: Command, decision: Decision) -> None:
        event_type = EventType.COMMAND_APPROVED if decision.approved else EventType.COMMAND_REJECTED
        self.events.append(
            Event(
                id=self._event_ids.next_id(),
                type=event_type,
                timestamp=self._clock.now(),
                payload={
                    "command_id": command.id,
                    "intersection_id": command.intersection_id,
                    "idempotency_key": command.idempotency_key,
                    "reason_code": decision.reason_code.value if decision.reason_code else None,
                    "explanation": decision.explanation,
                    "gate": True,
                },
            )
        )
