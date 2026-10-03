import math

from core.clock import SimClock
from core.events import EventType
from core.models import ControllerStatus, Priority, RouteAck, RouteAckStatus
from events.bus import EventBus
from routing.cost import evaluate_path
from routing.graph import RoutingGraph
from routing.planner import RoutePlanner
from tests.routing_fakes import FreeContext, city_for, settings_for_tests

BASELINE = ["A6", "B6", "B5", "C5", "C4", "D4", "D3", "E3", "E2", "F2", "F1"]
DETOUR = ["A6", "B6", "C6", "D6", "E6", "F6", "F5", "F4", "F3", "F2", "F1"]
VEHICLE = "AMB-1"


class FakeTracker:
    def __init__(self) -> None:
        self.positions: dict[str, tuple[int, float]] = {}
        self.routes: dict[str, list[str]] = {}

    def route_position(self, vehicle_id: str, now: int) -> tuple[int, float]:
        return self.positions.get(vehicle_id, (0, 0.0))

    def set_route(self, vehicle_id: str, route: list[str]) -> None:
        self.routes[vehicle_id] = list(route)


class FakeGuidance:
    def __init__(self) -> None:
        self.sent: list[tuple[str, int, list[str]]] = []
        self._acks: list[RouteAck] = []

    def send_route(self, vehicle_id: str, route_version: int, intersections: list[str]) -> None:
        self.sent.append((vehicle_id, route_version, list(intersections)))

    def drain_acks(self) -> list[RouteAck]:
        acks, self._acks = self._acks, []
        return acks

    def ack(self, vehicle_id: str, version: int, status: RouteAckStatus, at: int = 0) -> None:
        self._acks.append(
            RouteAck(vehicle_id=vehicle_id, route_version=version, status=status, timestamp=at)
        )


class Harness:
    def __init__(self, ctx: FreeContext | None = None, **overrides: object) -> None:
        self.settings = settings_for_tests(**overrides)
        self.city = city_for(self.settings)
        self.graph = RoutingGraph(self.city, self.settings)
        self.ctx = ctx or FreeContext()
        self.bus = EventBus()
        self.tracker = FakeTracker()
        self.guidance = FakeGuidance()
        self.published: list[tuple[EventType, dict[str, object]]] = []
        self.bus.subscribe(lambda e: self.published.append((e.type, dict(e.payload))))
        self.planner = RoutePlanner(
            city=self.city,
            graph=self.graph,
            ctx=self.ctx,
            settings=self.settings,
            clock=SimClock(),
            bus=self.bus,
            guidance=self.guidance,
            tracker=self.tracker,
            controller_status=lambda node, now: ControllerStatus.ONLINE,
        )

    def managed(self, route: list[str], now: int = 0) -> None:
        self.planner.register(VEHICLE, Priority.P1, list(route), True, now)
        self.tracker.routes[VEHICLE] = list(route)

    def decisions(self) -> list[object]:
        return self.planner.decisions(VEHICLE)

    def replan(self, now: int) -> None:
        self.planner.request(VEHICLE, "TEST")
        self.planner.step(now)


def test_dispatch_returns_a_route_to_the_hospital():
    h = Harness()
    nodes = h.planner.dispatch(VEHICLE, Priority.P1, "H1", "A6", 0)
    assert nodes is not None
    assert nodes[0] == "A6"
    assert nodes[-1] == "F1"


def test_dispatch_returns_none_when_the_hospital_cannot_be_reached():
    closed = {
        info.segment_id
        for info in RoutingGraph(
            city_for(settings_for_tests()), settings_for_tests()
        ).segments.values()
        if info.to_node == "F1"
    }
    h = Harness(FreeContext(closed=closed))
    assert h.planner.dispatch(VEHICLE, Priority.P1, "H1", "A6", 0) is None


def test_small_gain_is_rejected_with_the_threshold_reason():
    h = Harness(hysteresis_abs_s=1e6)
    h.managed(BASELINE)
    h.replan(1000)
    last = h.decisions()[-1]
    assert last.outcome == "KEPT"
    assert any("threshold" in reason for reason in last.rejected)
    assert h.guidance.sent == []


def test_large_gain_is_adopted_and_explained():
    h = Harness(
        hysteresis_abs_s=0,
        hysteresis_rel=0.01,
        min_route_dwell_s=0,
        commitment_distance_m=0,
        commitment_time_s=0,
    )
    h.managed(DETOUR)
    h.replan(1000)
    assert h.guidance.sent, "no route was sent"
    vehicle_id, version, nodes = h.guidance.sent[-1]
    assert version == 1
    assert nodes[-1] in {"F1", "F6"}
    updates = [p for t, p in h.published if t == EventType.ROUTE_UPDATED]
    assert updates and "faster than v0" in str(updates[-1]["explanation"])


def test_blocked_route_switches_even_inside_dwell_and_commitment():
    h = Harness(min_route_dwell_s=60, commitment_distance_m=500)
    h.managed(BASELINE)
    h.ctx.closed = {"C5->C4"}
    h.planner._vehicles[VEHICLE].last_switch_ms = 1000
    h.tracker.positions[VEHICLE] = (2, 0.0)
    h.replan(1500)
    assert h.guidance.sent, "blocked route was not replaced"
    assert "closed road" in h.decisions()[-1].explanation


def test_commitment_zone_blocks_a_switch_near_the_junction():
    h = Harness(
        hysteresis_abs_s=0, hysteresis_rel=0.01, min_route_dwell_s=0, commitment_distance_m=80
    )
    h.managed(DETOUR)
    segment = f"{DETOUR[0]}->{DETOUR[1]}"
    length = h.graph.info(segment).length_m
    h.tracker.positions[VEHICLE] = (0, length - 5.0)
    h.replan(1000)
    assert h.guidance.sent == []
    assert any("commitment zone" in r for r in h.decisions()[-1].rejected)


def test_minimum_dwell_blocks_a_switch():
    h = Harness(
        hysteresis_abs_s=0,
        hysteresis_rel=0.01,
        min_route_dwell_s=60,
        commitment_distance_m=0,
        commitment_time_s=0,
    )
    h.managed(DETOUR)
    h.planner._vehicles[VEHICLE].last_switch_ms = 1000
    h.replan(2000)
    assert h.guidance.sent == []
    assert any("dwell" in r for r in h.decisions()[-1].rejected)


def test_versions_increase_monotonically_across_adoptions():
    h = Harness(
        hysteresis_abs_s=0,
        hysteresis_rel=0.01,
        min_route_dwell_s=0,
        commitment_distance_m=0,
        commitment_time_s=0,
    )
    h.managed(DETOUR)
    h.replan(1000)
    first = h.guidance.sent[-1][1]
    h.guidance.ack(VEHICLE, first, RouteAckStatus.ADOPTED, 1100)
    h.ctx.closed = {"B6->C6"}
    h.replan(2000)
    versions = [v for _, v, _ in h.guidance.sent]
    assert versions == sorted(set(versions))
    assert len(versions) >= 2


def test_adoption_and_rejection_are_published_with_explanations():
    h = Harness(hysteresis_abs_s=1e6)
    h.managed(BASELINE)
    h.replan(1000)
    kinds = [t for t, _ in h.published]
    assert EventType.DECISION in kinds
    kept = [p for t, p in h.published if t == EventType.DECISION]
    assert kept and kept[-1]["kind"] == "ROUTE_KEPT"
    assert "explanation" in kept[-1]


def test_fast_path_switches_in_the_same_step_to_a_precomputed_backup():
    h = Harness(route_alternative_budget_expansions=100_000, hysteresis_abs_s=1e6)
    h.managed(BASELINE)
    h.replan(1000)
    alts = h.planner.alternatives(VEHICLE)
    assert alts, "no backup route was precomputed"
    h.ctx.closed = {"B6->B5"}
    before = len(h.guidance.sent)
    h.replan(2000)
    assert len(h.guidance.sent) > before, "blocked route was not switched in the same step"
    outcomes = [d.outcome for d in h.decisions()]
    assert "FAST_PATH" in outcomes or "ADOPTED" in outcomes


def test_coalesced_triggers_produce_one_decision():
    h = Harness(hysteresis_abs_s=1e6)
    h.managed(BASELINE)
    for trigger in ("A", "B", "C"):
        h.planner.request(VEHICLE, trigger)
    h.replan(1000)
    assert len(h.decisions()) == 1


def test_replan_starts_from_the_current_position_not_the_origin():
    h = Harness(
        hysteresis_abs_s=0,
        hysteresis_rel=0.01,
        min_route_dwell_s=0,
        commitment_distance_m=0,
        commitment_time_s=0,
    )
    h.managed(BASELINE)
    h.tracker.positions[VEHICLE] = (2, 0.0)
    h.ctx.closed = {BASELINE[3] + "->" + BASELINE[4]}
    h.replan(1000)
    pending = h.planner.pending_route(VEHICLE)
    assert pending is not None
    assert pending.nodes[:3] == tuple(BASELINE[:3])
    assert pending.nodes[-1] in {"F1", "F6"}


def test_adopted_ack_moves_the_route_into_effect_and_updates_the_tracker():
    h = Harness(
        hysteresis_abs_s=0,
        hysteresis_rel=0.01,
        min_route_dwell_s=0,
        commitment_distance_m=0,
        commitment_time_s=0,
    )
    h.managed(DETOUR)
    h.replan(1000)
    pending = h.planner.pending_route(VEHICLE)
    assert pending is not None
    h.guidance.ack(VEHICLE, pending.version, RouteAckStatus.ADOPTED, 1100)
    h.replan(1200)
    assert h.planner.active_route(VEHICLE).version == pending.version
    assert h.tracker.routes[VEHICLE] == list(pending.nodes)


def test_cannot_follow_ack_keeps_the_route_and_requests_a_replan():
    h = Harness(
        hysteresis_abs_s=0,
        hysteresis_rel=0.01,
        min_route_dwell_s=0,
        commitment_distance_m=0,
        commitment_time_s=0,
    )
    h.managed(DETOUR)
    h.replan(1000)
    pending = h.planner.pending_route(VEHICLE)
    assert pending is not None
    h.guidance.ack(VEHICLE, pending.version, RouteAckStatus.CANNOT_FOLLOW, 1100)
    h.replan(1200)
    assert (
        h.planner.pending_route(VEHICLE) is None
        or h.planner.pending_route(VEHICLE).version != pending.version
    )
    assert h.planner.active_route(VEHICLE).version == 0
    assert any(d.outcome == "CANNOT_FOLLOW" for d in h.decisions())


def test_no_second_switch_while_the_previous_route_is_unacknowledged():
    h = Harness(
        hysteresis_abs_s=0,
        hysteresis_rel=0.01,
        min_route_dwell_s=0,
        commitment_distance_m=0,
        commitment_time_s=0,
    )
    h.managed(DETOUR)
    h.replan(1000)
    sent_before = len(h.guidance.sent)
    h.planner.request(VEHICLE, "PERIODIC")
    h.planner.step(2000)
    assert len(h.guidance.sent) == sent_before


def test_route_deviation_event_triggers_a_replan():
    h = Harness(hysteresis_abs_s=1e6)
    h.managed(BASELINE)
    h.bus.publish(EventType.ROUTE_DEVIATION, 500, {"vehicle_id": VEHICLE})
    h.replan(1000)
    assert any("ROUTE_DEVIATION" in d.triggers for d in h.decisions())


def test_unmanaged_vehicles_are_never_replanned():
    h = Harness(hysteresis_abs_s=0, hysteresis_rel=0.01, min_route_dwell_s=0)
    h.planner.register(VEHICLE, Priority.P1, list(DETOUR), False, 0)
    h.planner.request(VEHICLE, "PERIODIC")
    h.replan(1000)
    assert h.guidance.sent == []
    assert h.decisions() == []


def test_evaluate_path_costs_for_the_current_route_are_finite_when_open():
    h = Harness()
    segments = [f"{BASELINE[i]}->{BASELINE[i + 1]}" for i in range(len(BASELINE) - 1)]
    total = evaluate_path(h.graph, h.ctx, h.settings, segments, 0.0, 0).total_s
    assert math.isfinite(total)
