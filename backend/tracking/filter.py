"""One-dimensional, along-route state filter for an emergency vehicle
(PRD FR-TRK-01, §11.8).

State is `s` (metres along the route) and `v` (metres/second). An
alpha-beta filter smooths noisy position into those two numbers: it
predicts forward at the current speed, then corrects position by `alpha`
and speed by `beta / dt` times the prediction residual. ADR-021 records why
this rather than a full Kalman filter (explainable, two tunable constants,
and the 1-D along-route state needs nothing more).

Speed for ETA also gets an EWMA of observed speed, separate from the filter's
own speed estimate, so an ETA can use a slower-moving average.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.clock import SimTimeMs


@dataclass
class AlphaBetaState:
    s_m: float
    v_mps: float
    t_ms: SimTimeMs


class AlphaBetaFilter:
    def __init__(self, alpha: float, beta: float) -> None:
        if not (0 < alpha < 1 and 0 < beta < 1):
            raise ValueError("alpha and beta must be in (0, 1)")
        self.alpha = alpha
        self.beta = beta
        self.state: AlphaBetaState | None = None

    def initialize(self, s_m: float, v_mps: float, t_ms: SimTimeMs) -> None:
        self.state = AlphaBetaState(s_m=s_m, v_mps=max(0.0, v_mps), t_ms=t_ms)

    def predict(self, t_ms: SimTimeMs) -> float:
        """Position predicted at `t_ms` without correcting."""
        if self.state is None:
            raise RuntimeError("filter not initialised")
        dt_s = max(0.0, (t_ms - self.state.t_ms) / 1000.0)
        return self.state.s_m + self.state.v_mps * dt_s

    def update(self, measured_s_m: float, t_ms: SimTimeMs) -> AlphaBetaState:
        if self.state is None:
            self.initialize(measured_s_m, 0.0, t_ms)
            assert self.state is not None
            return self.state
        dt_s = (t_ms - self.state.t_ms) / 1000.0
        if dt_s <= 0:
            return self.state  # no time has passed: nothing to correct against
        predicted = self.state.s_m + self.state.v_mps * dt_s
        residual = measured_s_m - predicted
        new_s = predicted + self.alpha * residual
        new_v = max(0.0, self.state.v_mps + (self.beta / dt_s) * residual)
        self.state = AlphaBetaState(s_m=new_s, v_mps=new_v, t_ms=t_ms)
        return self.state

    def coast(self, t_ms: SimTimeMs) -> AlphaBetaState:
        """Advance without a measurement (dead reckoning): position moves at
        the last filtered speed; speed itself is held."""
        if self.state is None:
            raise RuntimeError("filter not initialised")
        self.state = AlphaBetaState(s_m=self.predict(t_ms), v_mps=self.state.v_mps, t_ms=t_ms)
        return self.state


class EwmaSpeed:
    def __init__(self, alpha: float) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = alpha
        self.value: float | None = None

    def observe(self, speed_mps: float) -> float:
        if self.value is None:
            self.value = speed_mps
        else:
            self.value = self.alpha * speed_mps + (1 - self.alpha) * self.value
        return self.value
