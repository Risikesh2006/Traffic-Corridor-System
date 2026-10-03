import asyncio
from pathlib import Path

from core.general_city_loader import load_general_city
from core.clock import SimClock
from core.config import load_settings
from core.models import Command, CommandAck, CommandType, ControllerStatus
from safety.gate import SafeSignalLink
from safety.supervisor import IntersectionView, SafetySupervisor

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class FakeSignalLink:
    def __init__(self) -> None:
        self.sent: list[Command] = []

    async def send(self, command: Command) -> None:
        self.sent.append(command)

    def drain_acks(self) -> list[CommandAck]:
        return []

    def controller_status(self, intersection_id: str) -> ControllerStatus:
        return ControllerStatus.ONLINE


GRID_PATH = CONFIG_DIR / "cities" / "grid.city.json"


def _node_phase_id() -> str:
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_general_city(GRID_PATH, settings)
    return city.intersection("B2").phases[0].id


def _setup():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    city = load_general_city(GRID_PATH, settings)
    clock = SimClock()
    supervisor = SafetySupervisor(city, settings, clock, secret="test-secret")
    link = FakeSignalLink()

    def view_source(intersection_id: str) -> IntersectionView:
        return IntersectionView(
            intersection_id=intersection_id,
            controller_status=ControllerStatus.ONLINE,
            reported_at=clock.now(),
            current_phase_id=_node_phase_id(),
            red_since={},
        )

    gate = SafeSignalLink(link, supervisor, view_source, clock)
    return gate, supervisor, link, clock


def _preempt(idempotency_key="v1:B2:0001") -> Command:
    return Command(
        id=f"cmd-{idempotency_key}",
        type=CommandType.PREEMPT,
        intersection_id="B2",
        plan_version=1,
        idempotency_key=idempotency_key,
        payload={"phase_id": _node_phase_id(), "hold_until": 20_000},
        created_at=0,
    )


def test_approved_command_flows_through_and_is_signed():
    gate, supervisor, link, clock = _setup()
    decision = asyncio.run(gate.send(_preempt()))
    assert decision.approved
    assert len(link.sent) == 1
    assert link.sent[0].approval == decision.approval


def test_supervisor_crash_rejects_everything_and_sends_nothing():
    gate, supervisor, link, clock = _setup()
    supervisor.crash()
    decision = asyncio.run(gate.send(_preempt()))
    assert not decision.approved
    assert decision.reason_code.value == "SUPERVISOR_UNAVAILABLE"
    assert link.sent == []


def test_supervisor_missed_heartbeat_also_fails_closed():
    gate, supervisor, link, clock = _setup()
    clock.advance(supervisor.settings.heartbeat_timeout_ms + 1000)
    decision = asyncio.run(gate.send(_preempt()))
    assert not decision.approved
    assert decision.reason_code.value == "SUPERVISOR_UNAVAILABLE"
    assert link.sent == []


def test_restart_resumes_flow():
    gate, supervisor, link, clock = _setup()
    supervisor.crash()
    asyncio.run(gate.send(_preempt(idempotency_key="v1:B2:0001")))
    assert link.sent == []

    supervisor.restart()
    decision = asyncio.run(gate.send(_preempt(idempotency_key="v1:B2:0002")))
    assert decision.approved
    assert len(link.sent) == 1


def test_rejected_command_never_sent():
    gate, supervisor, link, clock = _setup()
    bad = Command(
        id="cmd-bad",
        type=CommandType.PREEMPT,
        intersection_id="B2",
        plan_version=1,
        idempotency_key="v1:B2:0001",
        payload={"phase_id": "NOT_A_PHASE", "hold_until": 20_000},
        created_at=0,
    )
    decision = asyncio.run(gate.send(bad))
    assert not decision.approved
    assert link.sent == []


def test_gate_events_recorded():
    gate, supervisor, link, clock = _setup()
    asyncio.run(gate.send(_preempt()))
    events = gate.drain_events()
    assert len(events) == 1
    assert events[0].type.value == "COMMAND_APPROVED"
