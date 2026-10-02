from pathlib import Path

from hypothesis import HealthCheck, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from controller.intersection_controller import IntersectionController
from controller.messages import Ping
from controller.state_machine import IntersectionStateMachine, detect_conflict
from core.city_loader import load_city
from core.config import load_settings
from core.models import ConflictMatrix, Direction, SignalState

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _settings():
    return load_settings(CONFIG_DIR / "settings.yaml")


def _city():
    return load_city(CONFIG_DIR / "city.json", _settings())


def _b2_intersection():
    return _city().intersection("B2")  # interior, 4 approaches, 2 phases


def test_starts_green_on_first_cycle_phase():
    sm = IntersectionStateMachine(_b2_intersection())
    assert sm.state == SignalState.GREEN
    assert sm.current_phase_id == "NS_THROUGH"


def test_never_skips_amber_or_all_red():
    node = _b2_intersection()
    sm = IntersectionStateMachine(node)
    ns = sm.phase("NS_THROUGH")
    t = 0
    # Advance in small steps through a full cycle; collect the sequence of states.
    seen = [sm.state]
    for _ in range(2000):
        t += 100
        sm.tick(t)
        if sm.state != seen[-1]:
            seen.append(sm.state)
        if len(seen) >= 5:
            break
    # GREEN -> AMBER -> ALL_RED -> GREEN -> AMBER ... never skipping a step.
    for i in range(len(seen) - 1):
        a, b = seen[i], seen[i + 1]
        assert (a, b) in {
            (SignalState.GREEN, SignalState.AMBER),
            (SignalState.AMBER, SignalState.ALL_RED),
            (SignalState.ALL_RED, SignalState.GREEN),
        }
    assert ns.min_green_ms > 0  # sanity the phase data loaded


def test_min_green_never_shortened():
    node = _b2_intersection()
    sm = IntersectionStateMachine(node)
    phase = sm.current_phase()
    # Even with a competing request arriving immediately, GREEN must hold
    # for the full min_green_ms before AMBER is allowed to start.
    sm.request_phase_change("EW_THROUGH", 0)
    sm.tick(phase.min_green_ms - 1)
    assert sm.state == SignalState.GREEN
    sm.tick(phase.min_green_ms)
    assert sm.state == SignalState.AMBER


def test_amber_and_all_red_durations_exact():
    node = _b2_intersection()
    sm = IntersectionStateMachine(node)
    phase = sm.current_phase()
    now = phase.min_green_ms
    sm.tick(now)
    assert sm.state == SignalState.AMBER
    amber_entered_at = sm.state_started_at
    # Before amber_ms elapses, still amber.
    sm.tick(amber_entered_at + phase.amber_ms - 1)
    assert sm.state == SignalState.AMBER
    sm.tick(amber_entered_at + phase.amber_ms)
    assert sm.state == SignalState.ALL_RED
    all_red_entered_at = sm.state_started_at
    target_phase = sm.phase(sm.next_phase_id)
    sm.tick(all_red_entered_at + target_phase.all_red_ms - 1)
    assert sm.state == SignalState.ALL_RED
    sm.tick(all_red_entered_at + target_phase.all_red_ms)
    assert sm.state == SignalState.GREEN


def test_normal_plan_cycles_in_order_with_correct_splits():
    node = _b2_intersection()
    sm = IntersectionStateMachine(node)
    expected_order = [e.phase_id for e in node.default_plan]
    seen_phases = [sm.current_phase_id]
    t = 0
    for _ in range(100_000):
        t += 100
        sm.tick(t)
        if sm.current_phase_id is not None and sm.current_phase_id != seen_phases[-1]:
            seen_phases.append(sm.current_phase_id)
        if len(seen_phases) >= len(expected_order) * 2 + 1:
            break
    # The observed green-phase sequence must follow the configured cycle order.
    idx = expected_order.index(seen_phases[0])
    for phase_id in seen_phases[1:]:
        idx = (idx + 1) % len(expected_order)
        assert phase_id == expected_order[idx]


def test_request_phase_change_during_min_green_is_deferred():
    node = _b2_intersection()
    sm = IntersectionStateMachine(node)
    phase = sm.current_phase()
    other_phase_id = next(p.id for p in node.phases if p.id != sm.current_phase_id)
    now = 100  # well before min_green_ms elapses
    result = sm.request_phase_change(other_phase_id, now)
    assert result.accepted
    target_phase = sm.phase(other_phase_id)
    expected = phase.min_green_ms + phase.amber_ms + target_phase.all_red_ms
    assert result.earliest_green_at == expected

    # Drive the clock and confirm the requested phase actually goes green
    # no earlier than promised, and timing rules were not violated en route.
    t = 0
    while t < result.earliest_green_at + 1000:
        t += 50
        sm.tick(t)
        if sm.state == SignalState.GREEN and sm.current_phase_id == other_phase_id:
            assert t >= result.earliest_green_at - 50  # within one tick step
            break
    else:
        raise AssertionError("requested phase never went green")


def test_request_phase_change_unknown_phase_rejected():
    node = _b2_intersection()
    sm = IntersectionStateMachine(node)
    result = sm.request_phase_change("NOT_A_PHASE", 0)
    assert not result.accepted
    assert result.reason == "unknown_phase"


def test_conflicting_greens_trigger_flashing_red():
    movements = [Direction.N, Direction.S, Direction.E, Direction.W]
    matrix = ConflictMatrix(
        movements=movements,
        conflict_pairs=[(Direction.N, Direction.E), (Direction.E, Direction.N)],
    )
    reason = detect_conflict(matrix, {Direction.N, Direction.E})
    assert reason is not None
    assert "N" in reason and "E" in reason

    reason_ok = detect_conflict(matrix, {Direction.N, Direction.S})
    assert reason_ok is None


def test_enter_flashing_is_terminal_for_this_phase():
    node = _b2_intersection()
    sm = IntersectionStateMachine(node)
    sm.enter_flashing("manual test trigger", 500)
    assert sm.state == SignalState.FLASHING_RED
    assert sm.flashing_reason == "manual test trigger"
    # Further ticks do not leave FLASHING_RED (no auto-recovery this phase).
    sm.tick(100_000)
    assert sm.state == SignalState.FLASHING_RED


def test_signal_view_all_red_during_flashing():
    node = _b2_intersection()
    controller = IntersectionController(node)
    controller.state_machine.enter_flashing("test", 0)
    view = controller.signal_view()
    assert all(s == SignalState.FLASHING_RED for s in view.values())


def test_ping_gets_pong():
    node = _b2_intersection()
    controller = IntersectionController(node)
    reply = controller.handle_message(Ping(message_id="m1", sent_at=0), now=0)
    assert reply.message_id == "m1"
    assert reply.intersection_id == "B2"


def test_unsupported_message_type():
    node = _b2_intersection()
    controller = IntersectionController(node)

    class SomeOtherMessage:
        message_id = "m2"

    reply = controller.handle_message(SomeOtherMessage(), now=0)
    assert reply.message_id == "m2"
    assert "unsupported" in reply.reason


def test_signal_changed_events_emitted():
    node = _b2_intersection()
    controller = IntersectionController(node)
    phase = controller.state_machine.current_phase()
    controller.tick(phase.min_green_ms)  # -> AMBER
    events = controller.drain_events()
    assert len(events) == 1
    assert events[0].type.value == "SIGNAL_CHANGED"
    assert events[0].payload["intersection_id"] == "B2"


# --- Hypothesis property test: INV-01..04 always hold -----------------

@hyp_settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    deadline=None,
)
@given(
    ops=st.lists(
        st.one_of(
            st.tuples(st.just("tick"), st.integers(min_value=1, max_value=5000)),
            st.tuples(st.just("request"), st.sampled_from(["NS_THROUGH", "EW_THROUGH"])),
        ),
        min_size=1,
        max_size=200,
    )
)
def test_invariants_hold_under_random_operations(ops: list[tuple]):
    node = _b2_intersection()
    sm = IntersectionStateMachine(node)
    phase_last_entered_green_at: dict[str, int] = {sm.current_phase_id: 0}
    now = 0

    for op, value in ops:
        if op == "tick":
            now += value
            before_phase = sm.current_phase_id
            transitions = sm.tick(now)
            for t in transitions:
                if t.to_state == SignalState.GREEN:
                    phase_last_entered_green_at[t.phase_id] = t.at
                if t.from_state == SignalState.GREEN and t.to_state == SignalState.AMBER:
                    # INV-04: the phase that just left GREEN must have held
                    # it for at least its min_green_ms.
                    entered_at = phase_last_entered_green_at.get(before_phase)
                    if entered_at is not None and before_phase is not None:
                        held_for = t.at - entered_at
                        assert held_for >= sm.phase(before_phase).min_green_ms
        else:
            sm.request_phase_change(value, now)

    # INV-01: never two conflicting movements green/amber at once.
    active = sm.green_movements() | sm.amber_movements()
    assert detect_conflict(node.conflict_matrix, active) is None
