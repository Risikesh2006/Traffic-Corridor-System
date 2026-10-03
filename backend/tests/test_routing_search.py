import heapq
import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from core.config import Settings
from routing.astar import search, straight_line_bound_s
from routing.cost import evaluate_path, step_cost
from routing.graph import RoutingGraph
from routing.yen import KShortestPaths
from tests.routing_fakes import FreeContext, city_for, settings_for_tests

FROZEN_FORECAST = {"forecast_tau_s": 1e9}


def _world(**overrides: object) -> tuple[Settings, RoutingGraph]:
    settings = settings_for_tests(**{**FROZEN_FORECAST, **overrides})
    return settings, RoutingGraph(city_for(settings), settings)


def _dijkstra_cost(
    graph: RoutingGraph,
    settings: Settings,
    ctx: FreeContext,
    starts: list[tuple[str, float]],
    goals: frozenset[str],
) -> float:
    frontier: list[tuple[float, str]] = []
    best: dict[str, float] = {}
    for sid, g0 in starts:
        if math.isfinite(g0):
            best[sid] = g0
            heapq.heappush(frontier, (g0, sid))
    while frontier:
        g, sid = heapq.heappop(frontier)
        if g > best.get(sid, math.inf):
            continue
        if graph.info(sid).to_node in goals:
            return g
        for transition in graph.successors[sid]:
            nxt = transition.to_segment
            step = step_cost(graph, ctx, settings, sid, nxt, 0.0, 0)
            if step.closed:
                continue
            ng = g + step.total()
            if ng < best.get(nxt, math.inf):
                best[nxt] = ng
                heapq.heappush(frontier, (ng, nxt))
    return math.inf


def test_straight_line_bound_is_zero_at_a_goal_and_positive_elsewhere():
    settings, graph = _world()
    node = "F1"
    assert straight_line_bound_s(graph, settings, node, frozenset({node})) == 0.0
    assert straight_line_bound_s(graph, settings, "A6", frozenset({node})) > 0.0


def test_heuristic_is_admissible_against_true_shortest_costs():
    settings, graph = _world()
    ctx = FreeContext()
    all_nodes = sorted({info.to_node for info in graph.segments.values()})
    for sid in sorted(graph.segments)[::7]:
        node = graph.info(sid).to_node
        for goal in all_nodes[::9]:
            if goal == node:
                continue
            true_cost = _dijkstra_cost(
                graph,
                settings,
                ctx,
                [
                    (
                        nxt.to_segment,
                        step_cost(graph, ctx, settings, sid, nxt.to_segment, 0.0, 0).total(),
                    )
                    for nxt in graph.successors[sid]
                ],
                frozenset({goal}),
            )
            bound = straight_line_bound_s(graph, settings, node, frozenset({goal}))
            assert bound <= true_cost + 1e-9, (sid, goal, bound, true_cost)


def test_heuristic_is_consistent_across_every_transition():
    settings, graph = _world()
    ctx = FreeContext()
    goals = frozenset({"F1", "F6"})
    for sid, transitions in graph.successors.items():
        for transition in transitions:
            nxt = transition.to_segment
            edge = step_cost(graph, ctx, settings, sid, nxt, 0.0, 0).total()
            h_here = straight_line_bound_s(graph, settings, graph.info(sid).to_node, goals)
            h_next = straight_line_bound_s(graph, settings, graph.info(nxt).to_node, goals)
            assert h_here <= edge + h_next + 1e-9


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=10_000),
    closures=st.integers(min_value=0, max_value=6),
    goal_pick=st.integers(min_value=0, max_value=5),
)
def test_astar_matches_dijkstra_optimum_on_random_congestion(
    seed: int, closures: int, goal_pick: int
):
    settings, graph = _world()
    sids = sorted(graph.segments)
    density = {sid: float((seed * 31 + i * 17) % 90) for i, sid in enumerate(sids)}
    queue = {sid: float((seed * 7 + i * 3) % 5) for i, sid in enumerate(sids)}
    closed = {sids[(seed + k * 23) % len(sids)] for k in range(closures)}
    ctx = FreeContext(density=density, queue=queue, closed=closed)
    hospitals = sorted({"F1", "F6", "A1", "C3"})
    goals = frozenset(
        {hospitals[goal_pick % len(hospitals)], hospitals[(goal_pick + 1) % len(hospitals)]}
    )
    start_sid = sids[seed % len(sids)]
    start_node = graph.info(start_sid).from_node
    starts = [
        (sid, step_cost(graph, ctx, settings, None, sid, 0.0, 0).total())
        for sid in graph.outgoing[start_node]
    ]
    result = search(graph, ctx, settings, starts, goals, now=0)
    reference = _dijkstra_cost(graph, settings, ctx, starts, goals)
    if math.isinf(reference):
        assert not result.found
    else:
        assert result.found
        assert math.isclose(result.total_s, reference, rel_tol=1e-6)
        assert not (set(result.segments) & closed)


def test_search_is_deterministic():
    settings, graph = _world()
    ctx = FreeContext(density={"B4->C4": 80.0})
    starts = [
        (sid, step_cost(graph, ctx, settings, None, sid, 0.0, 0).total())
        for sid in graph.outgoing["A6"]
    ]
    first = search(graph, ctx, settings, starts, frozenset({"F1"}), now=0)
    for _ in range(5):
        again = search(graph, ctx, settings, starts, frozenset({"F1"}), now=0)
        assert again.segments == first.segments
        assert again.total_s == first.total_s


def test_no_route_when_every_road_into_the_goal_is_closed():
    settings, graph = _world()
    into_goal = {info.segment_id for info in graph.segments.values() if info.to_node == "F1"}
    ctx = FreeContext(closed=into_goal)
    starts = [
        (sid, step_cost(graph, ctx, settings, None, sid, 0.0, 0).total())
        for sid in graph.outgoing["A6"]
    ]
    result = search(graph, ctx, settings, starts, frozenset({"F1"}), now=0)
    assert not result.found


def test_start_mid_segment_is_cheaper_by_the_part_already_driven():
    settings, graph = _world()
    ctx = FreeContext()
    sid = "A6->A5"
    length = graph.info(sid).length_m
    from_start = evaluate_path(graph, ctx, settings, [sid, "A5->A4"], 0.0, 0).total_s
    from_middle = evaluate_path(graph, ctx, settings, [sid, "A5->A4"], length / 2, 0).total_s
    assert from_middle < from_start


def test_multi_goal_returns_the_cheapest_reachable_goal():
    settings, graph = _world()
    ctx = FreeContext()
    starts = [
        (sid, step_cost(graph, ctx, settings, None, sid, 0.0, 0).total())
        for sid in graph.outgoing["A6"]
    ]
    both = search(graph, ctx, settings, starts, frozenset({"F1", "F6"}), now=0)
    solo = {
        goal: search(graph, ctx, settings, starts, frozenset({goal}), now=0).total_s
        for goal in ("F1", "F6")
    }
    assert math.isclose(both.total_s, min(solo.values()), rel_tol=1e-9)
    assert both.goal == min(solo, key=lambda g: solo[g])


def _enumerate_loopless(
    graph: RoutingGraph, start: str, goals: frozenset[str], max_len: int
) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []

    def walk(path: list[str], nodes: set[str]) -> None:
        last = graph.info(path[-1])
        if last.to_node in goals:
            paths.append(tuple(path))
        if len(path) >= max_len:
            return
        for transition in graph.successors[path[-1]]:
            nxt = graph.info(transition.to_segment)
            if nxt.to_node in nodes:
                continue
            walk(path + [transition.to_segment], nodes | {nxt.to_node})

    first = graph.info(start)
    walk([start], {first.from_node, first.to_node})
    return paths


def test_yen_matches_brute_force_k_cheapest_loopless_paths():
    settings, graph = _world(route_alternative_overlap_max=1.0)
    ctx = FreeContext(density={"A6->B6": 30.0, "B6->B5": 60.0}, queue={"B5->C5": 2.0})
    start = "A6->B6"
    goals = frozenset({"C4"})
    primary_result = search(
        graph,
        ctx,
        settings,
        [(start, step_cost(graph, ctx, settings, None, start, 0.0, 0, is_current=True).total())],
        goals,
        now=0,
    )
    primary = primary_result.segments
    yen = KShortestPaths(graph, ctx, settings, primary, 0.0, goals, 0, k=3)
    yen.advance(10_000_000)
    found = [yen.primary_cost()] + [alt.total_s for alt in yen.alternatives]

    brute = sorted(
        evaluate_path(graph, ctx, settings, list(p), 0.0, 0).total_s
        for p in _enumerate_loopless(graph, start, goals, max_len=12)
    )
    assert found == sorted(found)
    for a, b in zip(sorted(found), brute, strict=False):
        assert math.isclose(a, b, rel_tol=1e-9)


def test_yen_alternatives_respect_the_diversity_filter():
    settings, graph = _world()
    ctx = FreeContext()
    start = "A6->B6"
    goals = frozenset({"F1"})
    primary = search(
        graph,
        ctx,
        settings,
        [(start, step_cost(graph, ctx, settings, None, start, 0.0, 0, is_current=True).total())],
        goals,
        now=0,
    ).segments
    yen = KShortestPaths(graph, ctx, settings, primary, 0.0, goals, 0, k=3)
    yen.advance(10_000_000)
    costs = [alt.total_s for alt in yen.alternatives]
    assert costs == sorted(costs)
    for alt in yen.alternatives:
        assert alt.overlap < settings.route_alternative_overlap_max


def test_yen_result_does_not_depend_on_how_the_budget_is_spent():
    settings, graph = _world()
    ctx = FreeContext(density={"B4->C4": 50.0})
    start = "A6->B6"
    goals = frozenset({"F1"})
    primary = search(
        graph,
        ctx,
        settings,
        [(start, step_cost(graph, ctx, settings, None, start, 0.0, 0, is_current=True).total())],
        goals,
        now=0,
    ).segments

    all_at_once = KShortestPaths(graph, ctx, settings, primary, 0.0, goals, 0, k=3)
    all_at_once.advance(10_000_000)
    in_steps = KShortestPaths(graph, ctx, settings, primary, 0.0, goals, 0, k=3)
    while not in_steps.is_complete():
        in_steps.advance(25)
    assert [a.segments for a in in_steps.alternatives] == [
        a.segments for a in all_at_once.alternatives
    ]


def test_yen_stops_spending_budget_once_the_budget_is_used():
    settings, graph = _world()
    ctx = FreeContext()
    start = "A6->B6"
    goals = frozenset({"F1"})
    primary = search(
        graph,
        ctx,
        settings,
        [(start, step_cost(graph, ctx, settings, None, start, 0.0, 0, is_current=True).total())],
        goals,
        now=0,
    ).segments
    yen = KShortestPaths(graph, ctx, settings, primary, 0.0, goals, 0, k=3)
    yen.advance(1)
    after_first = yen.expanded_total
    assert after_first > 0
    assert not yen.is_complete()
    yen.advance(1)
    assert yen.expanded_total > after_first
