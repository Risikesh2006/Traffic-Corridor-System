"""Shared fixtures for routing tests: the real city and settings, plus a
controllable routing context so each test sets exactly the conditions it needs."""

from __future__ import annotations

from pathlib import Path

from core.clock import SimTimeMs
from core.config import Settings, load_settings
from core.general_city import GeneralCity
from core.general_city_loader import load_general_city
from core.models import ControllerStatus, SensorMetric

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def settings_for_tests(**overrides: object) -> Settings:
    base = load_settings(CONFIG_DIR / "settings.yaml")
    return base.model_copy(update=overrides)


def city_for(settings: Settings) -> GeneralCity:
    return load_general_city(CONFIG_DIR / "cities" / "grid.city.json", settings)


class FreeContext:
    """Routing context with per-segment density and queue, optional closures and
    controller statuses. Priors are zero and confidence is one unless changed."""

    def __init__(
        self,
        density: dict[str, float] | None = None,
        queue: dict[str, float] | None = None,
        confidence: float = 1.0,
        closed: set[str] | None = None,
        status: dict[str, ControllerStatus] | None = None,
        priors: dict[str, float] | None = None,
        red_wait_s: float = 30.0,
    ) -> None:
        self.density = density or {}
        self.queue = queue or {}
        self.confidence = confidence
        self.closed = closed or set()
        self.status = status or {}
        self.priors = priors or {}
        self.red_wait_s = red_wait_s

    def live(self, segment_id: str, metric: SensorMetric, now: SimTimeMs) -> tuple[float, float]:
        table = self.density if metric == SensorMetric.DENSITY else self.queue
        return table.get(segment_id, 0.0), self.confidence

    def prior(self, segment_id: str, metric: SensorMetric, sim_time: SimTimeMs) -> float:
        return self.priors.get(segment_id, 0.0)

    def is_closed(self, segment_id: str) -> bool:
        return segment_id in self.closed

    def controller_status(self, node_id: str, now: SimTimeMs) -> ControllerStatus:
        return self.status.get(node_id, ControllerStatus.ONLINE)

    def normal_red_wait_s(self, node_id: str) -> float:
        return self.red_wait_s
