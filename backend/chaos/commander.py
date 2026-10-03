"""Sends random PREEMPT/RENEW/RELEASE commands through the SafeSignalLink
gate to random intersections, for the full-simulation safety test and the
`safety_report` script (PRD Phase 3 Parts D4/E). Shared so both draw from
exactly the same behaviour.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from core.clock import SimTimeMs
from core.config import Settings
from core.general_city import GeneralCity
from core.models import Command, CommandAckStatus, CommandType
from core.rng import RngFactory
from safety.gate import SafeSignalLink

_COMMAND_TYPES = (CommandType.PREEMPT, CommandType.RENEW, CommandType.RELEASE)


class ChaoticCommander:
    def __init__(
        self,
        city: GeneralCity,
        intersection_ids: list[str],
        settings: Settings,
        rng: RngFactory,
        gate: SafeSignalLink,
        interval_ms: int = 4000,
    ) -> None:
        self._node_ids = sorted(intersection_ids)
        self._phases_by_node: dict[str, list[str]] = {
            node_id: sorted(p.id for p in city.intersection(node_id).phases)
            for node_id in self._node_ids
        }
        self.settings = settings
        self._rng = rng.stream("chaotic_commander")
        self.gate = gate
        self._interval_ms = interval_ms
        self._next_at = 0
        self._seq = 0

        self.sent = 0
        self.approved = 0
        self.rejected_by_reason: Counter[str] = Counter()
        self.ack_by_status: Counter[str] = Counter()

    def step(self, now: SimTimeMs) -> None:
        """Call every tick; fires a command at most once per interval."""
        if now < self._next_at:
            self._drain_acks()
            return
        self._next_at = now + self._interval_ms
        asyncio.run(self._send_one(now))
        self._drain_acks()

    async def _send_one(self, now: SimTimeMs) -> None:
        node_id = self._rng.choice(self._node_ids)
        command_type = self._rng.choice(_COMMAND_TYPES)
        self._seq += 1
        plan_version = self._seq
        idempotency_key = f"v{plan_version}:{node_id}:{self._seq % 10000:04d}"

        payload: dict[str, object] = {}
        if command_type in (CommandType.PREEMPT, CommandType.RENEW):
            hold_s = self._rng.uniform(5, self.settings.lease_duration_s * 1.5)
            payload["hold_until"] = now + round(hold_s * 1000)
        if command_type == CommandType.PREEMPT:
            payload["phase_id"] = self._rng.choice(self._phases_by_node[node_id])

        cmd = Command(
            id=f"chaos-cmd-{self._seq}",
            type=command_type,
            intersection_id=node_id,
            plan_version=plan_version,
            idempotency_key=idempotency_key,
            payload=payload,
            created_at=now,
        )
        decision = await self.gate.send(cmd)
        self.sent += 1
        if decision.approved:
            self.approved += 1
        else:
            reason = decision.reason_code.value if decision.reason_code else "UNKNOWN"
            self.rejected_by_reason[reason] += 1

    def _drain_acks(self) -> None:
        for ack in self.gate.drain_acks():
            self.ack_by_status[ack.status.value] += 1

    @property
    def executed(self) -> int:
        return self.ack_by_status.get(CommandAckStatus.EXECUTED.value, 0)

    @property
    def duplicates(self) -> int:
        return self.ack_by_status.get(CommandAckStatus.DUPLICATE.value, 0)

    @property
    def stale_version_ignored(self) -> int:
        return self.ack_by_status.get(CommandAckStatus.IGNORED_STALE_VERSION.value, 0)
