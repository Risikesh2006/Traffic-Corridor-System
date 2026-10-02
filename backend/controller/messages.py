"""Low-level messages the controller can receive/reply over the network.

Command handling (PREEMPT/RENEW/RELEASE) is added in Phase 3; for Phase 2
the controller only answers PING, used by health/connectivity checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.clock import SimTimeMs


@dataclass(frozen=True)
class Ping:
    message_id: str
    sent_at: SimTimeMs


@dataclass(frozen=True)
class Pong:
    message_id: str
    intersection_id: str
    replied_at: SimTimeMs


@dataclass(frozen=True)
class Unsupported:
    message_id: str
    reason: str
