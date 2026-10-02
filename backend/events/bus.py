"""Minimal in-process event bus (PRD Phase 4 Part A1).

Synchronous publish/subscribe, deterministic delivery: subscribers are
called in registration order, and every event is delivered FIFO relative
to the order it was published. No persistence, no replay — the event
store arrives in Phase 8; `EventBus` is deliberately shaped so a store can
subscribe to it then without any publisher changing (a store is just
another subscriber that happens to also keep a copy).
"""

from __future__ import annotations

from collections.abc import Callable

from core.clock import SimTimeMs
from core.events import Event, EventIdFactory, EventType

Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self, event_ids: EventIdFactory | None = None) -> None:
        self._event_ids = event_ids or EventIdFactory()
        self._subscribers: list[Subscriber] = []
        self._log: list[Event] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def publish(
        self,
        event_type: EventType,
        timestamp: SimTimeMs,
        payload: dict[str, object],
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Event:
        """Publishes a new event, assigning it a deterministic ID. If
        `correlation_id` is omitted and `causation_id` is given, the new
        event inherits its cause's correlation_id (a derived event belongs
        to the same causal chain as whatever caused it)."""
        if correlation_id is None and causation_id is not None:
            correlation_id = self._correlation_of(causation_id)
        event = Event(
            id=self._event_ids.next_id(),
            type=event_type,
            timestamp=timestamp,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        self._dispatch(event)
        return event

    def _correlation_of(self, causation_id: str) -> str | None:
        for event in reversed(self._log):
            if event.id == causation_id:
                return event.correlation_id or event.id
        return None

    def _dispatch(self, event: Event) -> None:
        self._log.append(event)
        for subscriber in self._subscribers:
            subscriber(event)

    def history(self) -> list[Event]:
        return list(self._log)
