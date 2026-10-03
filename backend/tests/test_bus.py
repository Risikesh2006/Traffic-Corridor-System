from core.events import EventType
from events.bus import EventBus


def test_subscribers_called_in_registration_order():
    calls: list[str] = []
    bus = EventBus()
    bus.subscribe(lambda e: calls.append("first"))
    bus.subscribe(lambda e: calls.append("second"))
    bus.subscribe(lambda e: calls.append("third"))
    bus.publish(EventType.HEARTBEAT, timestamp=0, payload={})
    assert calls == ["first", "second", "third"]


def test_events_processed_fifo():
    received: list[int] = []
    bus = EventBus()
    bus.subscribe(lambda e: received.append(e.payload["n"]))
    for i in range(10):
        bus.publish(EventType.HEARTBEAT, timestamp=i, payload={"n": i})
    assert received == list(range(10))


def test_event_ids_are_deterministic_and_increasing():
    bus = EventBus()
    e1 = bus.publish(EventType.HEARTBEAT, timestamp=0, payload={})
    e2 = bus.publish(EventType.HEARTBEAT, timestamp=1, payload={})
    assert e1.id == "evt-000001"
    assert e2.id == "evt-000002"


def test_causation_id_set_and_correlation_inherited():
    bus = EventBus()
    root = bus.publish(EventType.GPS_FIX, timestamp=0, payload={})
    assert root.correlation_id is None

    derived = bus.publish(EventType.ROUTE_UPDATED, timestamp=1, payload={}, causation_id=root.id)
    assert derived.causation_id == root.id
    # Root had no correlation_id of its own, so the chain's correlation_id
    # becomes the root event's own id.
    assert derived.correlation_id == root.id

    grandchild = bus.publish(EventType.DECISION, timestamp=2, payload={}, causation_id=derived.id)
    assert grandchild.causation_id == derived.id
    assert grandchild.correlation_id == root.id  # same causal chain throughout


def test_explicit_correlation_id_overrides_inheritance():
    bus = EventBus()
    event = bus.publish(
        EventType.DECISION,
        timestamp=0,
        payload={},
        causation_id="some-other-event",
        correlation_id="explicit-correlation",
    )
    assert event.correlation_id == "explicit-correlation"


def test_history_returns_all_published_events_in_order():
    bus = EventBus()
    bus.publish(EventType.HEARTBEAT, timestamp=0, payload={"n": 1})
    bus.publish(EventType.HEARTBEAT, timestamp=1, payload={"n": 2})
    history = bus.history()
    assert [e.payload["n"] for e in history] == [1, 2]


def test_history_is_a_copy_not_the_live_log():
    bus = EventBus()
    bus.publish(EventType.HEARTBEAT, timestamp=0, payload={})
    history = bus.history()
    history.append("tampered")  # type: ignore[arg-type]
    assert len(bus.history()) == 1
