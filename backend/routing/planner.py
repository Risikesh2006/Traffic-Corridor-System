"""Route planner service (PRD FR-RT-04..07, 11.5; Phase 5 Part D).

The planner owns every managed vehicle's route. It replans from the vehicle's
tracked position (FR-RT-05), compares the best candidate against the current
route re-costed by the same model at the same time, and adopts the candidate
only under the stability rules below. Every decision, adopted or kept, is
recorded with a plain-English explanation.

Stability rules (anti-flapping, ADR-037):
  - A blocked route (a closed segment ahead) is always replaced, and this
    overrides the dwell and commitment rules.
  - Otherwise adopt only if the gain exceeds max(hysteresis_abs_s,
    hysteresis_rel x current remaining cost), at least min_route_dwell_s has
    passed since the last switch, and the vehicle is not inside the commitment
    zone of the next intersection.
  - Only one route is in flight at a time: nothing new is adopted until the
    vehicle acknowledges the last one (unless the current route is blocked).

Routes never revisit an intersection they have passed or already occupy. The
tracker looks up stop lines by node, so a looping route would be ambiguous.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from core.clock import Clock, SimTimeMs
from core.config import Settings
from core.events import Event, EventType
from core.interfaces import GuidanceLink
from core.models import City, ControllerStatus, HospitalStatus, Priority, RouteAckStatus
from events.bus import EventBus
from routing.astar import search
from routing.cost import RoutingContext, evaluate_path, step_cost
from routing.graph import RoutingGraph
from routing.yen import Alternative, KShortestPaths


class RouteTracker(Protocol):
    def route_position(self, vehicle_id: str, now: SimTimeMs) -> tuple[int, float]: ...

    def set_route(self, vehicle_id: str, route: list[str]) -> None: ...


@dataclass(frozen=True)
class RoutePlan:
    version: int
    nodes: tuple[str, ...]
    cost_s: float
    created_ms: SimTimeMs


@dataclass(frozen=True)
class RouteDecision:
    at_ms: SimTimeMs
    vehicle_id: str
    outcome: str
    triggers: tuple[str, ...]
    version_from: int
    version_to: int | None
    old_cost_s: float | None
    new_cost_s: float | None
    explanation: str
    rejected: tuple[str, ...] = ()
    expansions: int = 0


@dataclass
class _Vehicle:
    vehicle_id: str
    priority: Priority
    assigned: str | None
    managed: bool
    active: RoutePlan
    next_version: int
    pending: RoutePlan | None = None
    last_switch_ms: SimTimeMs | None = None
    last_plan_ms: SimTimeMs = 0
    expected_remaining_s: float | None = None
    expected_at_ms: SimTimeMs = 0
    pending_triggers: set[str] = field(default_factory=set)
    alt_key: tuple[int, str] | None = None
    alt_search: KShortestPaths | None = None
    alternatives: list[Alternative] = field(default_factory=list)
    decisions: list[RouteDecision] = field(default_factory=list)
    last_expansions: int = 0
    yen_expansions: int = 0


class RoutePlanner:
    def __init__(
        self,
        city: City,
        graph: RoutingGraph,
        ctx: RoutingContext,
        settings: Settings,
        clock: Clock,
        bus: EventBus,
        guidance: GuidanceLink,
        tracker: RouteTracker,
        controller_status: Callable[[str, SimTimeMs], ControllerStatus],
    ) -> None:
        self._city = city
        self._graph = graph
        self._ctx = ctx
        self._settings = settings
        self._clock = clock
        self._bus = bus
        self._guidance = guidance
        self._tracker = tracker
        self._controller_status = controller_status
        self._vehicles: dict[str, _Vehicle] = {}
        self._node_status: dict[str, ControllerStatus] = {}
        bus.subscribe(self._on_event)

    # -- registration and dispatch ---------------------------------------

    def register(
        self,
        vehicle_id: str,
        priority: Priority,
        nodes: list[str],
        managed: bool,
        now: SimTimeMs,
    ) -> None:
        self._vehicles[vehicle_id] = _Vehicle(
            vehicle_id=vehicle_id,
            priority=priority,
            assigned=None,
            managed=managed,
            active=RoutePlan(0, tuple(nodes), 0.0, now),
            next_version=1,
            last_plan_ms=now,
        )

    def dispatch(
        self,
        vehicle_id: str,
        priority: Priority,
        hospital_id: str | None,
        entry_node: str,
        now: SimTimeMs,
    ) -> list[str] | None:
        goals = self._goals(hospital_id)
        starts: list[tuple[str, float]] = []
        for segment_id in self._graph.outgoing[entry_node]:
            g0 = step_cost(self._graph, self._ctx, self._settings, None, segment_id, 0.0, now)
            if math.isfinite(g0.total()):
                starts.append((segment_id, g0.total()))
        result = search(self._graph, self._ctx, self._settings, starts, goals, now)
        if not result.found:
            return None
        nodes = (entry_node,) + tuple(self._graph.info(s).to_node for s in result.segments)
        plan = RoutePlan(1, nodes, result.total_s, now)
        self._vehicles[vehicle_id] = _Vehicle(
            vehicle_id=vehicle_id,
            priority=priority,
            assigned=hospital_id,
            managed=True,
            active=plan,
            next_version=2,
            last_plan_ms=now,
            expected_remaining_s=result.total_s,
            expected_at_ms=now,
        )
        self._record(
            self._vehicles[vehicle_id],
            RouteDecision(
                at_ms=now,
                vehicle_id=vehicle_id,
                outcome="DISPATCHED",
                triggers=("DISPATCH",),
                version_from=0,
                version_to=1,
                old_cost_s=None,
                new_cost_s=result.total_s,
                explanation=(
                    f"Dispatched via {self._via(nodes)}: {result.total_s:.0f} s to {nodes[-1]}"
                ),
                expansions=result.expanded,
            ),
        )
        return list(nodes)

    # -- triggers ------------------------------------------------------------

    def request(self, vehicle_id: str, trigger: str) -> None:
        vehicle = self._vehicles.get(vehicle_id)
        if vehicle is not None and vehicle.managed:
            vehicle.pending_triggers.add(trigger)

    def _on_event(self, event: Event) -> None:
        if event.type == EventType.ROUTE_DEVIATION:
            self.request(str(event.payload["vehicle_id"]), "ROUTE_DEVIATION")

    # -- one central step ---------------------------------------------------

    def step(self, now: SimTimeMs) -> None:
        self._process_acks(now)
        self._detect_triggers(now)
        for vehicle_id in sorted(self._vehicles):
            vehicle = self._vehicles[vehicle_id]
            if not vehicle.managed:
                continue
            if self._blocked(vehicle, now):
                self._fast_path(vehicle, now)
            if vehicle.pending_triggers:
                triggers = tuple(sorted(vehicle.pending_triggers))
                vehicle.pending_triggers.clear()
                self._plan(vehicle, now, triggers)
            self._advance_alternatives(vehicle, now)

    # -- queries --------------------------------------------------------------

    def active_route(self, vehicle_id: str) -> RoutePlan:
        return self._vehicles[vehicle_id].active

    def pending_route(self, vehicle_id: str) -> RoutePlan | None:
        return self._vehicles[vehicle_id].pending

    def alternatives(self, vehicle_id: str) -> list[Alternative]:
        return list(self._vehicles[vehicle_id].alternatives)

    def decisions(self, vehicle_id: str) -> list[RouteDecision]:
        return list(self._vehicles[vehicle_id].decisions)

    def last_expansions(self, vehicle_id: str) -> int:
        return self._vehicles[vehicle_id].last_expansions

    def yen_expansions(self, vehicle_id: str) -> int:
        return self._vehicles[vehicle_id].yen_expansions

    # -- internals ---------------------------------------------------------

    def _goals(self, hospital_id: str | None) -> frozenset[str]:
        if hospital_id is not None:
            hospital = next(h for h in self._city.hospitals if h.id == hospital_id)
            if hospital.status == HospitalStatus.AVAILABLE:
                return frozenset({hospital.node_id})
        return frozenset(
            h.node_id for h in self._city.hospitals if h.status == HospitalStatus.AVAILABLE
        )

    def _intended(self, vehicle: _Vehicle) -> RoutePlan:
        return vehicle.pending if vehicle.pending is not None else vehicle.active

    def _segments_ahead(self, nodes: tuple[str, ...], index: int) -> list[str]:
        return [f"{nodes[i]}->{nodes[i + 1]}" for i in range(index, len(nodes) - 1)]

    def _blocked(self, vehicle: _Vehicle, now: SimTimeMs) -> bool:
        nodes = self._intended(vehicle).nodes
        index, _ = self._tracker.route_position(vehicle.vehicle_id, now)
        ahead = self._segments_ahead(nodes, index)
        return any(self._ctx.is_closed(s) for s in ahead[1:])

    def _remaining_cost(self, vehicle: _Vehicle, now: SimTimeMs) -> float:
        nodes = self._intended(vehicle).nodes
        index, offset = self._tracker.route_position(vehicle.vehicle_id, now)
        ahead = self._segments_ahead(nodes, index)
        if not ahead:
            return 0.0
        return evaluate_path(self._graph, self._ctx, self._settings, ahead, offset, now).total_s

    def _plan(self, vehicle: _Vehicle, now: SimTimeMs, triggers: tuple[str, ...]) -> None:
        vehicle.last_plan_ms = now
        nodes = self._intended(vehicle).nodes
        index, offset = self._tracker.route_position(vehicle.vehicle_id, now)
        if index >= len(nodes) - 1:
            return
        ahead = self._segments_ahead(nodes, index)
        blocked = any(self._ctx.is_closed(s) for s in ahead[1:])
        current_eval = evaluate_path(self._graph, self._ctx, self._settings, ahead, offset, now)
        current_cost = math.inf if blocked else current_eval.total_s
        version_from = self._intended(vehicle).version
        if not blocked:
            vehicle.expected_remaining_s = current_cost
            vehicle.expected_at_ms = now

        passed = set(nodes[: index + 1])
        banned = frozenset(
            s.segment_id for s in self._graph.segments.values() if s.to_node in passed
        )
        seg0 = ahead[0]
        g0 = step_cost(
            self._graph,
            self._ctx,
            self._settings,
            None,
            seg0,
            0.0,
            now,
            fraction=self._fraction_remaining(seg0, offset),
            is_current=True,
        ).total()
        goals = self._goals(vehicle.assigned)
        result = search(
            self._graph, self._ctx, self._settings, [(seg0, g0)], goals, now, banned_segments=banned
        )
        vehicle.last_expansions = result.expanded

        if not result.found:
            self._record(
                vehicle,
                RouteDecision(
                    now,
                    vehicle.vehicle_id,
                    "NO_ROUTE",
                    triggers,
                    version_from,
                    None,
                    None,
                    None,
                    "No open route to an available hospital; keeping the current route",
                    expansions=result.expanded,
                ),
            )
            return

        candidate = result.segments
        cand_nodes = (self._graph.info(candidate[0]).from_node,) + tuple(
            self._graph.info(s).to_node for s in candidate
        )
        full_nodes = nodes[:index] + cand_nodes
        if len(set(full_nodes)) != len(full_nodes):
            self._keep(
                vehicle,
                now,
                triggers,
                current_cost,
                result.total_s,
                ("candidate would revisit an intersection",),
                result.expanded,
            )
            return
        if tuple(ahead) == candidate:
            return

        candidate_cost = result.total_s
        if blocked:
            self._adopt(
                vehicle,
                full_nodes,
                candidate_cost,
                now,
                triggers,
                current_cost,
                f"Switched to v{{new}} via {self._via(cand_nodes)}: the current route "
                f"v{version_from} contains a closed road",
                result.expanded,
            )
            return

        gain = current_cost - candidate_cost
        threshold = max(
            self._settings.hysteresis_abs_s, self._settings.hysteresis_rel * current_cost
        )
        rejects: list[str] = []
        if vehicle.pending is not None:
            rejects.append("previous route not yet acknowledged")
        elif gain <= threshold:
            rejects.append(f"{gain:.0f} s faster, below {threshold:.0f} s threshold")
        else:
            dwell_left = self._dwell_left_s(vehicle, now)
            if dwell_left > 0:
                rejects.append(f"minimum dwell: {dwell_left:.0f} s left")
            elif self._in_commitment_zone(ahead, offset):
                rejects.append("inside commitment zone of the next intersection")

        if rejects:
            self._keep(
                vehicle,
                now,
                triggers,
                current_cost,
                candidate_cost,
                tuple(rejects),
                result.expanded,
            )
            return
        self._adopt(
            vehicle,
            full_nodes,
            candidate_cost,
            now,
            triggers,
            current_cost,
            f"Switched to v{{new}} via {self._via(cand_nodes)}: {gain:.0f} s faster than "
            f"v{version_from}",
            result.expanded,
        )

    def _fast_path(self, vehicle: _Vehicle, now: SimTimeMs) -> None:
        nodes = self._intended(vehicle).nodes
        index, _ = self._tracker.route_position(vehicle.vehicle_id, now)
        current = self._segments_ahead(nodes, index)[0]
        for alt in vehicle.alternatives:
            if alt.segments[0] != current:
                continue
            if any(self._ctx.is_closed(s) for s in alt.segments[1:]):
                continue
            cand_nodes = (self._graph.info(alt.segments[0]).from_node,) + tuple(
                self._graph.info(s).to_node for s in alt.segments
            )
            full_nodes = nodes[:index] + cand_nodes
            if len(set(full_nodes)) != len(full_nodes):
                continue
            _, offset = self._tracker.route_position(vehicle.vehicle_id, now)
            cost = evaluate_path(
                self._graph, self._ctx, self._settings, alt.segments, offset, now
            ).total_s
            version_from = self._intended(vehicle).version
            self._adopt(
                vehicle,
                full_nodes,
                cost,
                now,
                ("FAST_PATH",),
                math.inf,
                f"Fast path: switched at once to v{{new}} via {self._via(cand_nodes)}, "
                f"a precomputed "
                f"backup; v{version_from} contains a closed road",
                0,
                outcome="FAST_PATH",
            )
            return

    def _adopt(
        self,
        vehicle: _Vehicle,
        nodes: tuple[str, ...],
        cost: float,
        now: SimTimeMs,
        triggers: tuple[str, ...],
        old_cost: float,
        template: str,
        expansions: int,
        outcome: str = "ADOPTED",
    ) -> None:
        version_from = self._intended(vehicle).version
        version = vehicle.next_version
        vehicle.next_version += 1
        plan = RoutePlan(version, nodes, cost, now)
        vehicle.pending = plan
        vehicle.last_switch_ms = now
        vehicle.alt_search = None
        vehicle.alt_key = None
        self._guidance.send_route(vehicle.vehicle_id, version, list(nodes))
        explanation = template.replace("{new}", str(version))
        decision = RouteDecision(
            at_ms=now,
            vehicle_id=vehicle.vehicle_id,
            outcome=outcome,
            triggers=triggers,
            version_from=version_from,
            version_to=version,
            old_cost_s=None if math.isinf(old_cost) else old_cost,
            new_cost_s=cost,
            explanation=explanation,
            expansions=expansions,
        )
        self._record(vehicle, decision)
        self._bus.publish(
            EventType.ROUTE_UPDATED,
            now,
            {
                "vehicle_id": vehicle.vehicle_id,
                "from_version": version_from,
                "to_version": version,
                "reason": ",".join(triggers),
                "explanation": explanation,
                "old_cost_s": decision.old_cost_s,
                "new_cost_s": cost,
            },
        )

    def _keep(
        self,
        vehicle: _Vehicle,
        now: SimTimeMs,
        triggers: tuple[str, ...],
        current_cost: float,
        candidate_cost: float,
        rejects: tuple[str, ...],
        expansions: int,
    ) -> None:
        version = self._intended(vehicle).version
        explanation = (
            f"Kept v{version}: candidate rejected ({'; '.join(rejects)})"
            if rejects
            else f"Kept v{version}"
        )
        decision = RouteDecision(
            at_ms=now,
            vehicle_id=vehicle.vehicle_id,
            outcome="KEPT",
            triggers=triggers,
            version_from=version,
            version_to=None,
            old_cost_s=None if math.isinf(current_cost) else current_cost,
            new_cost_s=candidate_cost,
            explanation=explanation,
            rejected=rejects,
            expansions=expansions,
        )
        self._record(vehicle, decision)
        self._bus.publish(
            EventType.DECISION,
            now,
            {
                "vehicle_id": vehicle.vehicle_id,
                "kind": "ROUTE_KEPT",
                "explanation": explanation,
                "triggers": list(triggers),
            },
        )

    def _record(self, vehicle: _Vehicle, decision: RouteDecision) -> None:
        vehicle.decisions.append(decision)

    def _process_acks(self, now: SimTimeMs) -> None:
        for ack in self._guidance.drain_acks():
            vehicle = self._vehicles.get(ack.vehicle_id)
            if vehicle is None or vehicle.pending is None:
                continue
            if ack.route_version != vehicle.pending.version:
                continue
            plan = vehicle.pending
            if ack.status == RouteAckStatus.ADOPTED:
                vehicle.active = plan
                vehicle.pending = None
                vehicle.expected_remaining_s = plan.cost_s
                vehicle.expected_at_ms = now
                self._tracker.set_route(vehicle.vehicle_id, list(plan.nodes))
                continue
            if ack.status == RouteAckStatus.CANNOT_FOLLOW:
                vehicle.pending = None
                version = vehicle.active.version
                explanation = (
                    f"Vehicle had already passed the divergence point of v{plan.version}; "
                    f"keeping v{version} and replanning from its true position"
                )
                self._record(
                    vehicle,
                    RouteDecision(
                        now,
                        vehicle.vehicle_id,
                        "CANNOT_FOLLOW",
                        ("CANNOT_FOLLOW",),
                        version,
                        plan.version,
                        None,
                        plan.cost_s,
                        explanation,
                    ),
                )
                self._bus.publish(
                    EventType.DECISION,
                    now,
                    {
                        "vehicle_id": vehicle.vehicle_id,
                        "kind": "CANNOT_FOLLOW",
                        "explanation": explanation,
                    },
                )
                vehicle.pending_triggers.add("CANNOT_FOLLOW")

    def _detect_triggers(self, now: SimTimeMs) -> None:
        for node in self._city.intersections:
            status = self._controller_status(node.id, now)
            previous = self._node_status.get(node.id, ControllerStatus.ONLINE)
            if status != previous:
                self._node_status[node.id] = status
                for vehicle in self._vehicles.values():
                    if vehicle.managed and node.id in self._intended(vehicle).nodes:
                        vehicle.pending_triggers.add(f"UNCONTROLLABLE:{node.id}")
        for vehicle_id in sorted(self._vehicles):
            vehicle = self._vehicles[vehicle_id]
            if not vehicle.managed:
                continue
            if vehicle.assigned is not None:
                hospital = next(h for h in self._city.hospitals if h.id == vehicle.assigned)
                if hospital.status != HospitalStatus.AVAILABLE:
                    vehicle.assigned = None
                    vehicle.pending_triggers.add("HOSPITAL_UNAVAILABLE")
            if now - vehicle.last_plan_ms >= self._settings.periodic_replan_ms:
                vehicle.pending_triggers.add("PERIODIC")
            if self._blocked(vehicle, now):
                vehicle.pending_triggers.add("ROAD_CLOSED")
            if vehicle.expected_remaining_s is not None and vehicle.pending is None:
                remaining = self._remaining_cost(vehicle, now)
                elapsed_s = (now - vehicle.expected_at_ms) / 1000.0
                expected = vehicle.expected_remaining_s - elapsed_s
                if (
                    expected > 0
                    and abs(remaining - expected) / expected > self._settings.eta_drift_fraction
                ):
                    vehicle.pending_triggers.add("ETA_DRIFT")

    def _advance_alternatives(self, vehicle: _Vehicle, now: SimTimeMs) -> None:
        if not vehicle.managed or vehicle.pending is not None:
            return
        nodes = self._intended(vehicle).nodes
        index, offset = self._tracker.route_position(vehicle.vehicle_id, now)
        if index >= len(nodes) - 1:
            return
        ahead = self._segments_ahead(nodes, index)
        key = (vehicle.active.version, ahead[0])
        if vehicle.alt_key != key:
            passed = set(nodes[: index + 1])
            base = frozenset(
                s.segment_id for s in self._graph.segments.values() if s.to_node in passed
            )
            previous = vehicle.alt_key
            vehicle.alt_key = key
            vehicle.alternatives = _rebase(vehicle.alternatives, previous, ahead[0])
            vehicle.alt_search = KShortestPaths(
                graph=self._graph,
                ctx=self._ctx,
                settings=self._settings,
                primary=tuple(ahead),
                start_offset_m=offset,
                goals=self._goals(vehicle.assigned),
                now=now,
                k=self._settings.k_alternatives,
                base_banned=base,
            )
        search_state = vehicle.alt_search
        if search_state is None or search_state.is_complete():
            return
        before = search_state.expanded_total
        search_state.advance(self._settings.route_alternative_budget_expansions)
        vehicle.yen_expansions += search_state.expanded_total - before
        vehicle.alternatives = list(search_state.alternatives)

    def _dwell_left_s(self, vehicle: _Vehicle, now: SimTimeMs) -> float:
        if vehicle.last_switch_ms is None:
            return 0.0
        elapsed = (now - vehicle.last_switch_ms) / 1000.0
        return max(0.0, self._settings.min_route_dwell_s - elapsed)

    def _in_commitment_zone(self, ahead: list[str], offset_m: float) -> bool:
        info = self._graph.info(ahead[0])
        distance = max(0.0, info.length_m - offset_m)
        speed = min(
            info.free_flow_mps * self._settings.ambulance_speed_factor,
            self._settings.ambulance_speed_cap_mps,
        )
        time_s = distance / speed
        return (
            distance < self._settings.commitment_distance_m
            or time_s < self._settings.commitment_time_s
        )

    def _fraction_remaining(self, segment_id: str, offset_m: float) -> float:
        length = self._graph.info(segment_id).length_m
        return max(0.0, length - offset_m) / length

    def _via(self, cand_nodes: tuple[str, ...]) -> str:
        return cand_nodes[1] if len(cand_nodes) > 2 else cand_nodes[-1]


def _rebase(
    alternatives: list[Alternative], previous: tuple[int, str] | None, current: str
) -> list[Alternative]:
    """Alternatives that go through the segment the vehicle has just entered,
    re-started from it. Anything else is no longer a valid backup."""
    if previous is None:
        return []
    rebased: list[Alternative] = []
    for alt in alternatives:
        if len(alt.segments) > 1 and alt.segments[0] == previous[1] and alt.segments[1] == current:
            rebased.append(Alternative(alt.segments[1:], alt.total_s, alt.overlap))
    return rebased
