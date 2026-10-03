"""Stateful test (PRD Phase 3 Part D3): a small system — several
controllers + SimNetwork (random latency/drops/duplicates/disconnects) +
SafetySupervisor + SafeSignalLink — driven by randomly ordered rules
(advance time, send random commands through the gate, inject network
faults, crash/restart the supervisor, trigger pedestrian calls). After
every rule, the InvariantChecker must report zero violations.

Run the thorough profile with:
    HYPOTHESIS_PROFILE=thorough pytest tests/test_safety_stateful.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from chaos.invariants import InvariantChecker
from controller.intersection_controller import IntersectionController
from core.general_city_loader import load_general_city
from core.clock import SimClock
from core.config import load_settings
from core.models import Command, CommandType, SignalState
from core.rng import RngFactory
from safety.gate import SafeSignalLink
from safety.supervisor import IntersectionView, SafetySupervisor
from sim.network import SimNetwork

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
GRID_PATH = CONFIG_DIR / "cities" / "grid.city.json"
SECRET = "stateful-test-secret"
INTERSECTIONS = ["A1", "B2", "C3", "D4"]


def _phase_ids_by_node() -> dict[str, list[str]]:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_general_city(GRID_PATH, settings)
    return {nid: [p.id for p in city.intersection(nid).phases] for nid in INTERSECTIONS}


_PHASE_IDS_BY_NODE = _phase_ids_by_node()
# Any phase id from any of the four junctions, plus a ghost id: a command
# naming a phase that belongs to a DIFFERENT intersection must still be
# handled safely (rejected as UNKNOWN_PHASE), so this is deliberately
# a global pool, not per-intersection.
PHASES = sorted({pid for ids in _PHASE_IDS_BY_NODE.values() for pid in ids}) + ["GHOST_PHASE"]
_VALID_PEDESTRIAN_CALLS = st.sampled_from(INTERSECTIONS).flatmap(
    lambda nid: st.tuples(st.just(nid), st.sampled_from(_PHASE_IDS_BY_NODE[nid]))
)


class CorridorSafetyMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        settings = load_settings(CONFIG_DIR / "settings.yaml")
        self.settings = settings
        self.city = city = load_general_city(GRID_PATH, settings)
        self.clock = SimClock()
        self.rng = RngFactory(4417)

        self.controllers: dict[str, IntersectionController] = {
            node_id: IntersectionController(
                city.intersection(node_id),
                hmac_secret=SECRET,
                max_hold_ms=settings.max_hold_ms,
                ped_walk_ms=settings.ped_walk_ms,
            )
            for node_id in INTERSECTIONS
        }
        self.network = SimNetwork(settings, self.rng, self.controllers)
        self.supervisor = SafetySupervisor(city, settings, self.clock, secret=SECRET)
        self.gate = SafeSignalLink(self.network, self.supervisor, self._view_of, self.clock)
        self.checker = InvariantChecker(city, settings, self.controllers, hmac_secret=SECRET)
        self._cmd_seq = 0

    def _view_of(self, intersection_id: str) -> IntersectionView:
        controller = self.controllers[intersection_id]
        status = self.network.controller_status(intersection_id)
        sm = controller.state_machine
        return IntersectionView(
            intersection_id=intersection_id,
            controller_status=status,
            reported_at=self.clock.now(),
            current_phase_id=sm.current_phase_id,
            red_since=dict(sm.red_since),
        )

    # -- rules -----------------------------------------------------------

    @rule(dt_ms=st.integers(min_value=100, max_value=5000))
    def advance_time(self, dt_ms: int) -> None:
        now = self.clock.advance(dt_ms)
        self.network.step(now)
        for node_id in sorted(self.controllers):
            self.controllers[node_id].tick(now)
        self.checker.on_tick(now)

    @rule(
        intersection_id=st.sampled_from(INTERSECTIONS),
        phase_id=st.sampled_from(PHASES),
        hold_offset=st.integers(min_value=-2000, max_value=60_000),
    )
    def send_preempt(self, intersection_id: str, phase_id: str, hold_offset: int) -> None:
        now = self.clock.now()
        self._cmd_seq += 1
        cmd = Command(
            id=f"cmd-{self._cmd_seq}",
            type=CommandType.PREEMPT,
            intersection_id=intersection_id,
            plan_version=self._cmd_seq % 4,  # occasionally out of order
            idempotency_key=f"v{self._cmd_seq % 4}:{intersection_id}:{self._cmd_seq % 6:04d}",
            payload={"phase_id": phase_id, "hold_until": now + hold_offset},
            created_at=now,
        )
        asyncio.run(self.gate.send(cmd))
        self.checker.on_tick(now)

    @rule(intersection_id=st.sampled_from(INTERSECTIONS))
    def send_release(self, intersection_id: str) -> None:
        now = self.clock.now()
        self._cmd_seq += 1
        cmd = Command(
            id=f"cmd-{self._cmd_seq}",
            type=CommandType.RELEASE,
            intersection_id=intersection_id,
            plan_version=self._cmd_seq % 4,
            idempotency_key=f"v{self._cmd_seq % 4}:{intersection_id}:{self._cmd_seq % 6:04d}",
            payload={},
            created_at=now,
        )
        asyncio.run(self.gate.send(cmd))
        self.checker.on_tick(now)

    @rule(intersection_id=st.sampled_from(INTERSECTIONS))
    def toggle_disconnect(self, intersection_id: str) -> None:
        if intersection_id in self.network._disconnected:  # noqa: SLF001 test introspection
            self.network.reconnect(intersection_id)
        else:
            self.network.disconnect(intersection_id)

    @rule(p=st.floats(min_value=0.0, max_value=0.5))
    def set_drop_rate(self, p: float) -> None:
        for intersection_id in INTERSECTIONS:
            self.network.set_drop(intersection_id, p)

    @rule()
    def crash_supervisor(self) -> None:
        self.supervisor.crash()

    @rule()
    def restart_supervisor(self) -> None:
        self.supervisor.restart()

    @rule()
    def heartbeat_supervisor(self) -> None:
        self.supervisor.heartbeat()

    @rule(call=_VALID_PEDESTRIAN_CALLS)
    def pedestrian_call(self, call: tuple[str, str]) -> None:
        intersection_id, phase_id = call
        now = self.clock.now()
        self.controllers[intersection_id].state_machine.register_pedestrian_call(phase_id, now)

    # -- invariant, checked after EVERY rule -------------------------------

    @invariant()
    def no_safety_violations(self) -> None:
        self.checker.assert_clean()

    @invariant()
    def controllers_never_flashing_without_reason(self) -> None:
        for controller in self.controllers.values():
            sm = controller.state_machine
            if sm.state == SignalState.FLASHING_RED:
                assert sm.flashing_reason is not None


TestCorridorSafety = CorridorSafetyMachine.TestCase
