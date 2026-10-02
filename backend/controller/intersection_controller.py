"""Wraps `IntersectionStateMachine` as the unit the rest of the system (sim
network, later the safety gate) actually talks to: ticking, reading what the
physical signal heads show, and answering messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from controller.messages import Ping, Pong, Unsupported
from controller.state_machine import IntersectionStateMachine, Transition
from core.clock import SimTimeMs
from core.events import Event, EventIdFactory, EventType
from core.models import Direction, Intersection, SignalState


@dataclass(frozen=True)
class MovementView:
    direction: Direction
    state: SignalState


class IntersectionController:
    def __init__(
        self, intersection: Intersection, event_ids: EventIdFactory | None = None
    ) -> None:
        self.intersection_id = intersection.id
        self._intersection = intersection
        self._sm = IntersectionStateMachine(intersection)
        self._event_ids = event_ids or EventIdFactory()
        self.events: list[Event] = []

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
        return transitions

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

    def handle_message(self, message: object, now: SimTimeMs) -> Pong | Unsupported:
        if isinstance(message, Ping):
            return Pong(
                message_id=message.message_id,
                intersection_id=self.intersection_id,
                replied_at=now,
            )
        message_id = getattr(message, "message_id", "unknown")
        return Unsupported(message_id=message_id, reason="unsupported message type")
