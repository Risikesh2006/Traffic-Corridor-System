import math

from core.models import ControllerStatus
from routing.cost import evaluate_path, step_cost
from routing.graph import RoutingGraph, Turn
from tests.routing_fakes import FreeContext, city_for, settings_for_tests

NOW = 0


def _setup(**overrides: object) -> tuple[RoutingGraph, object]:
    settings = settings_for_tests(**overrides)
    return RoutingGraph(city_for(settings), settings), settings


def _a_segment_and_successor(graph: RoutingGraph) -> tuple[str, str]:
    for sid, transitions in sorted(graph.successors.items()):
        for transition in transitions:
            if transition.turn == Turn.THROUGH:
                return sid, transition.to_segment
    raise AssertionError("no through transition in the test city")


def test_every_component_is_non_negative():
    graph, settings = _setup()
    ctx = FreeContext(density={"A6->A5": 40.0}, queue={"A6->A5": 4.0}, confidence=0.3)
    prev, sid = _a_segment_and_successor(graph)
    bd = step_cost(graph, ctx, settings, prev, sid, 0.0, NOW)
    for value in (
        bd.travel_s,
        bd.queue_s,
        bd.signal_s,
        bd.turn_s,
        bd.uncertainty_s,
        bd.uncontrollable_s,
    ):
        assert value >= 0.0


def test_closed_segment_costs_infinity():
    graph, settings = _setup()
    prev, sid = _a_segment_and_successor(graph)
    ctx = FreeContext(closed={sid})
    bd = step_cost(graph, ctx, settings, prev, sid, 0.0, NOW)
    assert bd.closed
    assert math.isinf(bd.total())


def test_the_segment_the_vehicle_is_on_is_never_treated_as_closed():
    graph, settings = _setup()
    sid = "A6->A5"
    ctx = FreeContext(closed={sid})
    bd = step_cost(graph, ctx, settings, None, sid, 0.0, NOW, is_current=True)
    assert not bd.closed
    assert math.isfinite(bd.total())


def test_congestion_increases_travel_time():
    graph, settings = _setup()
    sid = "A6->A5"
    free = step_cost(graph, FreeContext(), settings, None, sid, 0.0, NOW).travel_s
    jammed = step_cost(
        graph, FreeContext(density={sid: 120.0}), settings, None, sid, 0.0, NOW
    ).travel_s
    assert jammed > free


def test_stale_data_never_makes_a_road_cheaper():
    graph, settings = _setup()
    sid = "A6->A5"
    fresh = step_cost(graph, FreeContext(confidence=1.0), settings, None, sid, 0.0, NOW).total()
    stale = step_cost(graph, FreeContext(confidence=0.0), settings, None, sid, 0.0, NOW).total()
    assert stale >= fresh
    assert stale > fresh


def test_uncontrollable_downstream_node_adds_the_configured_penalty():
    graph, settings = _setup()
    sid = "A6->A5"
    node = graph.info(sid).to_node
    ok = step_cost(graph, FreeContext(), settings, None, sid, 0.0, NOW)
    bad = step_cost(
        graph,
        FreeContext(status={node: ControllerStatus.UNREACHABLE}),
        settings,
        None,
        sid,
        0.0,
        NOW,
    )
    assert ok.uncontrollable_s == 0.0
    assert bad.uncontrollable_s == settings.uncontrollable_penalty_s


def test_normal_signal_delay_is_charged_at_every_stop_line():
    graph, settings = _setup()
    sid = "A6->A5"
    bd = step_cost(graph, FreeContext(red_wait_s=20.0), settings, None, sid, 0.0, NOW)
    assert bd.signal_s == 20.0


def test_turn_penalties_follow_the_turn_class():
    graph, settings = _setup()
    seen = {}
    for sid, transitions in graph.successors.items():
        for transition in transitions:
            seen.setdefault(transition.turn, (sid, transition.to_segment))
    through = seen[Turn.THROUGH]
    assert step_cost(graph, FreeContext(), settings, *through, 0.0, NOW).turn_s == (
        settings.turn_penalty_through_s
    )
    near = seen[Turn.NEAR_SIDE]
    assert step_cost(graph, FreeContext(), settings, *near, 0.0, NOW).turn_s == (
        settings.turn_penalty_near_side_s
    )


def test_time_aware_cost_uses_current_density_when_entry_is_now():
    graph, settings = _setup()
    sid = "A6->A5"
    ctx = FreeContext(density={sid: 120.0}, priors={sid: 0.0})
    now_cost = step_cost(graph, ctx, settings, None, sid, 0.0, NOW).travel_s
    assert now_cost > step_cost(graph, FreeContext(), settings, None, sid, 0.0, NOW).travel_s


def test_time_aware_cost_falls_back_to_history_far_in_the_future():
    graph, settings = _setup()
    sid = "A6->A5"
    ctx = FreeContext(density={sid: 120.0}, priors={sid: 0.0})
    far = step_cost(graph, ctx, settings, None, sid, 1e6, NOW).travel_s
    free = step_cost(graph, FreeContext(), settings, None, sid, 0.0, NOW).travel_s
    assert math.isclose(far, free, rel_tol=1e-6)


def test_partial_first_segment_charges_only_the_remaining_share():
    graph, settings = _setup()
    sid = "A6->A5"
    length = graph.info(sid).length_m
    whole = evaluate_path(graph, FreeContext(), settings, [sid], 0.0, NOW).total_s
    half = evaluate_path(graph, FreeContext(), settings, [sid], length / 2, NOW).total_s
    assert math.isclose(half, whole / 2, rel_tol=1e-9)


def test_evaluate_path_matches_stepwise_accumulation():
    graph, settings = _setup()
    path = ["A6->A5", "A5->A4"]
    ctx = FreeContext(density={"A5->A4": 60.0}, queue={"A5->A4": 3.0})
    total = evaluate_path(graph, ctx, settings, path, 0.0, NOW).total_s
    first = step_cost(graph, ctx, settings, None, path[0], 0.0, NOW, is_current=True).total()
    second = step_cost(graph, ctx, settings, path[0], path[1], first, NOW).total()
    assert math.isclose(total, first + second, rel_tol=1e-12)
