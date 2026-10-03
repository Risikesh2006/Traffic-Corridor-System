"""Simulated network implementing `SignalLink` between central code and
every intersection controller (PRD FR-SIM-07).

Deliveries are scheduled at deterministic sim times on a `(deliver_at,
sequence)`-ordered heap — `sequence` is a global monotonic counter, so two
messages queued in the same tick still get a stable, reproducible order.
Reordering between distinct messages emerges naturally: each message's
jitter is drawn independently, so a message sent slightly later can still
be delivered earlier if its jitter draw is smaller.
"""

from __future__ import annotations

import heapq

from controller.intersection_controller import IntersectionController
from controller.messages import Ping, Pong
from core.clock import SimTimeMs
from core.config import Settings
from core.models import (
    Command,
    CommandAck,
    CommandAckStatus,
    ControllerStatus,
    SignalState,
    StatusReport,
)
from core.rng import RngFactory


class SimNetwork:
    def __init__(
        self,
        settings: Settings,
        rng: RngFactory,
        controllers: dict[str, IntersectionController],
    ) -> None:
        self.settings = settings
        self._controllers = controllers
        self._latency_rng = rng.stream("network_latency")
        self._fault_rng = rng.stream("network_fault")

        ids = list(controllers)
        self._base_latency_ms = dict.fromkeys(ids, settings.network_latency_base_ms)
        self._jitter_ms = dict.fromkeys(ids, settings.network_latency_jitter_ms)
        self._drop_p = dict.fromkeys(ids, settings.network_drop_probability)
        self._dup_p = dict.fromkeys(ids, settings.network_duplicate_probability)
        self._disconnected: set[str] = set()

        self._queue: list[tuple[SimTimeMs, int, str, object]] = []
        self._seq = 0
        self._now: SimTimeMs = 0
        self._pending_acks: list[CommandAck] = []
        self._pending_pongs: list[Pong] = []
        self._uplink: list[tuple[SimTimeMs, int, str, object]] = []
        self._status_inbox: list[StatusReport] = []

    # -- fault controls -----------------------------------------------------

    def disconnect(self, intersection_id: str) -> None:
        self._disconnected.add(intersection_id)

    def reconnect(self, intersection_id: str) -> None:
        self._disconnected.discard(intersection_id)

    def set_drop(self, intersection_id: str, p: float) -> None:
        self._drop_p[intersection_id] = p

    def set_duplicate(self, intersection_id: str, p: float) -> None:
        self._dup_p[intersection_id] = p

    def set_latency(self, intersection_id: str, base_ms: int, jitter_ms: int) -> None:
        self._base_latency_ms[intersection_id] = base_ms
        self._jitter_ms[intersection_id] = jitter_ms

    # -- sending --------------------------------------------------------

    async def send(self, command: Command) -> None:
        self._enqueue(command.intersection_id, command)

    def ping(self, intersection_id: str, now: SimTimeMs) -> None:
        """Not part of SignalLink — a connectivity-check convenience used
        by health checks and tests."""
        self._enqueue(intersection_id, Ping(message_id=f"ping-{self._seq}", sent_at=now))

    def send_status(self, report: StatusReport) -> None:
        """Up-link: a controller reports its state to central code. Subject to
        the same latency, drops and duplicates as any other message."""
        self._enqueue(report.intersection_id, report, uplink=True)

    def _enqueue(self, intersection_id: str, message: object, uplink: bool = False) -> None:
        if intersection_id in self._disconnected:
            return
        if self._fault_rng.random() < self._drop_p.get(intersection_id, 0.0):
            return
        copies = 2 if self._fault_rng.random() < self._dup_p.get(intersection_id, 0.0) else 1
        base = self._base_latency_ms.get(intersection_id, self.settings.network_latency_base_ms)
        jitter = self._jitter_ms.get(intersection_id, self.settings.network_latency_jitter_ms)
        target = self._uplink if uplink else self._queue
        for _ in range(copies):
            offset = self._latency_rng.uniform(-jitter, jitter) if jitter > 0 else 0.0
            delay_ms = max(0, round(base + offset))
            deliver_at = self._now + delay_ms
            self._seq += 1
            heapq.heappush(target, (deliver_at, self._seq, intersection_id, message))

    # -- delivery ------------------------------------------------------

    def step(self, now: SimTimeMs) -> None:
        self._now = now
        while self._uplink and self._uplink[0][0] <= now:
            _, _, _, report = heapq.heappop(self._uplink)
            assert isinstance(report, StatusReport)
            self._status_inbox.append(report)
        while self._queue and self._queue[0][0] <= now:
            _, _, intersection_id, message = heapq.heappop(self._queue)
            controller = self._controllers.get(intersection_id)
            if controller is None:
                continue
            reply = controller.handle_message(message, now)
            if isinstance(message, Command):
                self._pending_acks.append(self._to_ack(message, reply, now))
            elif isinstance(reply, Pong):
                self._pending_pongs.append(reply)

    def _to_ack(self, command: Command, reply: object, now: SimTimeMs) -> CommandAck:
        if isinstance(reply, CommandAck):
            return reply
        # Defensive fallback: handle_message() should always return a real
        # CommandAck for a Command input. Only reachable if a future message
        # type is misrouted here.
        return CommandAck(
            command_id=command.id,
            intersection_id=command.intersection_id,
            idempotency_key=command.idempotency_key,
            status=CommandAckStatus.REJECTED,
            reason_code="UNEXPECTED_REPLY_TYPE",
            timestamp=now,
        )

    # -- SignalLink interface -----------------------------------------------

    def drain_acks(self) -> list[CommandAck]:
        acks, self._pending_acks = self._pending_acks, []
        return acks

    def drain_status_reports(self) -> list[StatusReport]:
        reports, self._status_inbox = self._status_inbox, []
        return reports

    def drain_pongs(self) -> list[Pong]:
        pongs, self._pending_pongs = self._pending_pongs, []
        return pongs

    def controller_status(self, intersection_id: str) -> ControllerStatus:
        if intersection_id in self._disconnected:
            return ControllerStatus.UNREACHABLE
        controller = self._controllers[intersection_id]
        if controller.state_machine.state == SignalState.FLASHING_RED:
            return ControllerStatus.FLASHING
        return ControllerStatus.ONLINE
