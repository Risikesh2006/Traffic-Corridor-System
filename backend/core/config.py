"""Typed runtime configuration (PRD §18 tunables + §14.1 latency targets).

Every tunable value used by control-system logic must live here — no magic
numbers scattered through modules. Durations are authored in seconds in
`config/settings.yaml` (human-friendly) and exposed as precomputed integer
milliseconds via `*_ms` properties (ADR-001: all sim time is integer ms).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


def to_ms(seconds: float) -> int:
    return round(seconds * 1000)


class Settings(BaseModel):
    # --- Signal timing (PRD §18) ---
    amber_s: float = Field(default=4, ge=3, le=6)
    all_red_s: float = Field(default=2, gt=0)
    min_green_s: float = Field(default=10, gt=0)
    ped_clearance_s: float = Field(default=7, ge=0)

    # --- Corridor / preemption ---
    max_hold_s: float = Field(default=90, gt=0)
    lease_duration_s: float = Field(default=30, gt=0)
    lookahead_horizon_s: float = Field(default=90, gt=0)
    headway_s: float = Field(default=2.0, gt=0)
    startup_loss_s: float = Field(default=2.0, ge=0)
    base_buffer_s: float = Field(default=3, ge=0)

    # --- Data freshness ---
    tau_density_s: float = Field(default=30, gt=0)
    tau_gps_s: float = Field(default=5, gt=0)
    density_ttl_s: float = Field(default=60, gt=0)
    gps_loss_threshold_s: float = Field(default=3, gt=0)
    pessimism_factor: float = Field(default=1.2, ge=1.0)

    # --- Routing ---
    uncontrollable_penalty_s: float = Field(default=45, ge=0)
    hysteresis_abs_s: float = Field(default=15, ge=0)
    hysteresis_rel: float = Field(default=0.10, gt=0, lt=1)
    k_alternatives: int = Field(default=3, ge=1)

    # --- Control loop timing ---
    corridor_tick_s: float = Field(default=1, gt=0)
    periodic_replan_s: float = Field(default=10, gt=0)
    heartbeat_interval_s: float = Field(default=1, gt=0)
    heartbeat_timeout_s: float = Field(default=3, gt=0)

    # --- Validation limits ---
    max_gps_speed_mps: float = Field(default=50, gt=0)
    max_gps_jump_m: float = Field(
        default=300, gt=0, description="implausible single-fix displacement regardless of dt"
    )
    clock_skew_tolerance_s: float = Field(
        default=0.2, ge=0, description="future-timestamp tolerance before clamping (FR-ING)"
    )

    # --- Historical profile (state/history.py) ---
    history_bucket_minutes: int = Field(default=15, gt=0)
    history_seeds: list[int] = Field(default_factory=lambda: [4417, 1, 2, 3, 4])

    # --- Tracking (tracking/) ---
    alpha_beta_alpha: float = Field(default=0.6, gt=0, lt=1)
    alpha_beta_beta: float = Field(default=0.2, gt=0, lt=1)
    ewma_speed_alpha: float = Field(default=0.3, gt=0, le=1)
    map_match_radius_m: float = Field(default=30, gt=0)
    deviation_fix_count: int = Field(default=3, ge=1)
    deviation_distance_m: float = Field(default=40, gt=0)
    deviation_duration_s: float = Field(default=5, gt=0)
    stop_line_margin_m: float = Field(default=5, ge=0)
    eta_base_window_s: float = Field(default=5, ge=0)
    dead_reckoning_growth_mps: float = Field(
        default=2.0, ge=0, description="position uncertainty growth per s without GPS"
    )
    eta_k_gps: float = Field(default=0.5, ge=0, description="window growth per s of GPS age")
    eta_k_speed: float = Field(
        default=1.0, ge=0, description="window growth per s of travel x speed std"
    )
    eta_k_wait: float = Field(
        default=0.5,
        ge=0,
        description="window growth per unit projected red wait (signal plan uncertainty)",
    )
    eta_k_stale: float = Field(
        default=0.2, ge=0, description="window growth per unit stale fraction"
    )
    eta_expected_red_fraction: float = Field(
        default=0.5, ge=0, le=1, description="probability of meeting red at a stop line"
    )

    # --- Phase generation (core/phasing.py, Phase 6) ---
    through_tolerance_deg: float = Field(default=30, gt=0, lt=90)
    uturn_threshold_deg: float = Field(default=150, gt=90, le=180)
    regular_bearing_tolerance_deg: float = Field(default=15, ge=0, lt=90)
    cycle_target_s: float = Field(default=90, gt=0)
    cycle_min_s: float = Field(default=60, gt=0)
    cycle_max_s: float = Field(default=150, gt=0)
    phase_green_max_s: float = Field(default=60, gt=0)

    # --- Routing (routing/, Phase 5) ---
    ambulance_speed_cap_mps: float = Field(default=20.0, gt=0)
    bpr_alpha: float = Field(default=0.15, ge=0)
    bpr_beta: float = Field(default=4.0, gt=0)
    route_critical_density_fraction: float = Field(default=0.5, gt=0, le=1)
    route_x_max: float = Field(default=1.5, gt=0)
    congestion_sensitivity: float = Field(default=0.5, ge=0, le=1)
    driving_side: Literal["left", "right"] = "left"
    allow_u_turns: bool = False
    turn_penalty_through_s: float = Field(default=0, ge=0)
    turn_penalty_near_side_s: float = Field(default=3, ge=0)
    turn_penalty_far_side_s: float = Field(default=5, ge=0)
    uncertainty_weight: float = Field(default=0.2, ge=0)
    forecast_tau_s: float = Field(default=120, gt=0)
    route_alternative_overlap_max: float = Field(default=0.7, gt=0, le=1)
    route_alternative_budget_expansions: int = Field(default=400, ge=1)
    commitment_distance_m: float = Field(default=80, ge=0)
    commitment_time_s: float = Field(default=8, ge=0)
    min_route_dwell_s: float = Field(default=20, ge=0)
    eta_drift_fraction: float = Field(default=0.15, gt=0)
    reaction_delay_s: float = Field(default=2, ge=0)
    guidance_latency_s: float = Field(default=0.03, ge=0)

    # --- Simulation ---
    sim_ticks_per_second: int = Field(default=10, ge=1)
    default_seed: int = Field(default=4417)

    # --- Traffic / queue discharge (sim/traffic.py) ---
    jam_spacing_m: float = Field(default=7.5, gt=0, description="metres of road per queued vehicle")
    amber_discharge_fraction: float = Field(
        default=0.5, ge=0, le=1, description="fraction of amber during which discharge continues"
    )
    turn_through_prob: float = Field(default=0.70, ge=0, le=1)
    turn_left_prob: float = Field(default=0.15, ge=0, le=1)
    turn_right_prob: float = Field(default=0.15, ge=0, le=1)
    yield_factor: float = Field(
        default=0.0, ge=0, le=1, description="fraction of queued vehicles that yield to ambulance"
    )
    ambulance_speed_factor: float = Field(default=1.2, gt=0)

    # --- Demand (sim/demand.py) ---
    demand_base_vph: float = Field(default=300, ge=0, description="normal-road entry rate, veh/h")
    demand_arterial_multiplier: float = Field(default=1.5, gt=0)
    demand_internal_source_vph: float = Field(default=30, ge=0)
    demand_peak_multiplier: float = Field(default=1.0, gt=0)
    demand_hourly_multipliers: list[float] = Field(
        default_factory=lambda: [
            0.20,
            0.15,
            0.10,
            0.10,
            0.15,
            0.30,  # 00-05: night trough
            0.60,
            0.90,
            1.00,
            0.70,
            0.55,
            0.55,  # 06-11: morning peak at 08
            0.60,
            0.55,
            0.50,
            0.55,
            0.65,
            0.85,  # 12-17: moderate, 14:00 ~ 0.50
            1.00,
            0.80,
            0.55,
            0.45,
            0.35,
            0.25,  # 18-23: evening peak at 18
        ],
        description="24 weekday hour-of-day multipliers on demand_base_vph",
    )

    # --- Sensors (sim/sensors.py) ---
    sensor_interval_s: float = Field(default=5, gt=0)
    sensor_density_noise_std: float = Field(default=0.5, ge=0, description="veh/km/lane std dev")

    # --- Vehicle feed (sim/vehicle_feed.py) ---
    gps_interval_s: float = Field(default=1, gt=0)
    gps_noise_std_m: float = Field(default=5.0, ge=0)
    map_origin_lat: float = Field(default=12.9716, ge=-90, le=90)
    map_origin_lng: float = Field(default=77.5946, ge=-180, le=180)

    # --- Network (sim/network.py) ---
    network_latency_base_ms: int = Field(default=20, ge=0)
    network_latency_jitter_ms: int = Field(default=10, ge=0)
    network_drop_probability: float = Field(default=0.0, ge=0, le=1)
    network_duplicate_probability: float = Field(default=0.0, ge=0, le=1)

    # --- Safety (backend/safety/, backend/controller/) ---
    hmac_secret: str = Field(default="dev-only-insecure-secret-do-not-use-in-production")
    idempotency_store_size: int = Field(default=1000, ge=1)
    command_rate_limit_per_s: float = Field(default=5.0, gt=0)

    # --- Pedestrians (sim/pedestrians.py, controller ped-clearance floor) ---
    ped_walk_s: float = Field(default=5.0, ge=0)
    ped_call_rate_per_min: float = Field(default=2.0, ge=0)

    # --- Latency targets, p95 (PRD §14.1) ---
    latency_input_to_state_ms: int = Field(default=50, gt=0)
    latency_corridor_tick_ms: int = Field(default=100, gt=0)
    latency_full_replan_ms: int = Field(default=300, gt=0)
    latency_closure_to_commands_ms: int = Field(default=500, gt=0)
    latency_safety_validation_ms: int = Field(default=5, gt=0)
    latency_state_to_ui_ms: int = Field(default=250, gt=0)

    @model_validator(mode="after")
    def _check_all_red_lt_min_green(self) -> Settings:
        if self.all_red_s >= self.min_green_s:
            raise ValueError("all_red_s must be shorter than min_green_s")
        if self.heartbeat_timeout_s <= self.heartbeat_interval_s:
            raise ValueError("heartbeat_timeout_s must exceed heartbeat_interval_s")
        turn_total = self.turn_through_prob + self.turn_left_prob + self.turn_right_prob
        if abs(turn_total - 1.0) > 1e-6:
            raise ValueError(f"turn probabilities must sum to 1.0, got {turn_total}")
        if len(self.demand_hourly_multipliers) != 24:
            raise ValueError(
                f"demand_hourly_multipliers must have 24 entries, got "
                f"{len(self.demand_hourly_multipliers)}"
            )
        return self

    # --- ms accessors (ADR-001) ---
    @property
    def amber_ms(self) -> int:
        return to_ms(self.amber_s)

    @property
    def all_red_ms(self) -> int:
        return to_ms(self.all_red_s)

    @property
    def min_green_ms(self) -> int:
        return to_ms(self.min_green_s)

    @property
    def ped_clearance_ms(self) -> int:
        return to_ms(self.ped_clearance_s)

    @property
    def max_hold_ms(self) -> int:
        return to_ms(self.max_hold_s)

    @property
    def lease_duration_ms(self) -> int:
        return to_ms(self.lease_duration_s)

    @property
    def lookahead_horizon_ms(self) -> int:
        return to_ms(self.lookahead_horizon_s)

    @property
    def startup_loss_ms(self) -> int:
        return to_ms(self.startup_loss_s)

    @property
    def tau_density_ms(self) -> int:
        return to_ms(self.tau_density_s)

    @property
    def tau_gps_ms(self) -> int:
        return to_ms(self.tau_gps_s)

    @property
    def density_ttl_ms(self) -> int:
        return to_ms(self.density_ttl_s)

    @property
    def gps_loss_threshold_ms(self) -> int:
        return to_ms(self.gps_loss_threshold_s)

    @property
    def corridor_tick_ms(self) -> int:
        return to_ms(self.corridor_tick_s)

    @property
    def periodic_replan_ms(self) -> int:
        return to_ms(self.periodic_replan_s)

    @property
    def heartbeat_interval_ms(self) -> int:
        return to_ms(self.heartbeat_interval_s)

    @property
    def heartbeat_timeout_ms(self) -> int:
        return to_ms(self.heartbeat_timeout_s)

    @property
    def sim_tick_ms(self) -> int:
        return round(1000 / self.sim_ticks_per_second)

    @property
    def sensor_interval_ms(self) -> int:
        return to_ms(self.sensor_interval_s)

    @property
    def gps_interval_ms(self) -> int:
        return to_ms(self.gps_interval_s)

    @property
    def ped_walk_ms(self) -> int:
        return to_ms(self.ped_walk_s)

    @property
    def clock_skew_tolerance_ms(self) -> int:
        return to_ms(self.clock_skew_tolerance_s)

    @property
    def deviation_duration_ms(self) -> int:
        return to_ms(self.deviation_duration_s)

    @property
    def eta_base_window_ms(self) -> int:
        return to_ms(self.eta_base_window_s)

    @property
    def cycle_target_ms(self) -> int:
        return to_ms(self.cycle_target_s)

    @property
    def cycle_min_ms(self) -> int:
        return to_ms(self.cycle_min_s)

    @property
    def cycle_max_ms(self) -> int:
        return to_ms(self.cycle_max_s)

    @property
    def phase_green_max_ms(self) -> int:
        return to_ms(self.phase_green_max_s)

    @property
    def reaction_delay_ms(self) -> int:
        return to_ms(self.reaction_delay_s)

    @property
    def guidance_latency_ms(self) -> int:
        return to_ms(self.guidance_latency_s)

    @property
    def commitment_time_ms(self) -> int:
        return to_ms(self.commitment_time_s)

    @property
    def min_route_dwell_ms(self) -> int:
        return to_ms(self.min_route_dwell_s)

    @property
    def forecast_tau_ms(self) -> int:
        return to_ms(self.forecast_tau_s)

    def demand_rate_vph(self, hour_of_day: int, is_arterial: bool) -> float:
        """Entry rate (vehicles/hour) for one source, from the hour-of-day
        profile, scenario peak override, and the arterial multiplier."""
        multiplier = self.demand_hourly_multipliers[hour_of_day % 24]
        rate = self.demand_base_vph * multiplier * self.demand_peak_multiplier
        return rate * self.demand_arterial_multiplier if is_arterial else rate


def load_settings(path: str | Path) -> Settings:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    settings = Settings(**data)
    secret_override = os.environ.get("CORRIDOR_HMAC_SECRET")
    if secret_override:
        settings = settings.model_copy(update={"hmac_secret": secret_override})
    return settings
