from routing.graph import RoutingGraph, Turn, classify_turn
from tests.routing_fakes import city_for, settings_for_tests


def _graph(**overrides: object) -> RoutingGraph:
    settings = settings_for_tests(**overrides)
    return RoutingGraph(city_for(settings), settings)


def test_successors_start_where_the_segment_ends():
    graph = _graph()
    for sid, transitions in graph.successors.items():
        for transition in transitions:
            assert graph.info(transition.to_segment).from_node == graph.info(sid).to_node


def test_u_turns_are_banned_by_default():
    graph = _graph()
    for sid, info in graph.segments.items():
        reverse = f"{info.to_node}->{info.from_node}"
        assert reverse not in {t.to_segment for t in graph.successors[sid]}


def test_u_turns_can_be_enabled_by_configuration():
    graph = _graph(allow_u_turns=True)
    sid = next(iter(graph.segments))
    info = graph.info(sid)
    reverse = f"{info.to_node}->{info.from_node}"
    assert reverse in {t.to_segment for t in graph.successors[sid]}
    assert graph.turn_between(sid, reverse) == Turn.U_TURN


def test_straight_on_is_through():
    assert classify_turn((1, 0), (1, 0), "left") == Turn.THROUGH


def test_left_and_right_are_classified_in_screen_orientation():
    east_then_north = classify_turn((1, 0), (0, -1), "left")
    east_then_south = classify_turn((1, 0), (0, 1), "left")
    assert east_then_north == Turn.NEAR_SIDE
    assert east_then_south == Turn.FAR_SIDE


def test_driving_side_swaps_which_turn_is_near_side():
    assert classify_turn((1, 0), (0, -1), "right") == Turn.FAR_SIDE
    assert classify_turn((1, 0), (0, 1), "right") == Turn.NEAR_SIDE


def test_u_turn_is_recognised_by_opposing_headings():
    assert classify_turn((1, 0), (-1, 0), "left") == Turn.U_TURN


def test_turn_between_reports_the_transition_turn():
    graph = _graph()
    for sid, transitions in graph.successors.items():
        for transition in transitions:
            assert graph.turn_between(sid, transition.to_segment) == transition.turn
