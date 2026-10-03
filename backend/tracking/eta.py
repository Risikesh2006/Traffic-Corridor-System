"""ETA prediction for each upcoming stop line on the route (PRD FR-TRK-03,
§11.8). Two deliberately different definitions, both needed:

eta_unimpeded  — arrival if every signal were green and every queue already
                 cleared: distance at the vehicle's cruise speed (free-flow
                 times the ambulance factor). The current, possibly stopped,
                 speed is deliberately NOT used: a stop is an impediment.
                 Corridor planning (Phase 6) uses this as the earliest
                 possible arrival; it is a LOWER BOUND on real arrival.
eta_expected   — departure through the stop line under normal operation: the
                 later of the unimpeded arrival and the next green on this
                 approach (projected from the controller's reported signal
                 plan), plus the queue ahead discharging. Used for the hospital
                 ETA and the baseline comparison.

Confidence window (formula, all times in ms):
  centre_low  = min(eta_unimpeded, eta_expected)
  centre_high = max(eta_unimpeded, eta_expected)
  half_width  = eta_base_window
              + k_gps    * gps_age_s
              + k_speed  * speed_std_mps * travel_s
              + k_stale  * stale_fraction * expected_s
              + k_wait   * projected_red_wait_s
  eta_low  = max(now, centre_low  - half_width)
  eta_high =          centre_high + half_width

Because centre_low <= eta_unimpeded, the window always contains the
unimpeded ETA, and eta_low <= eta_high by construction. Calibration (how
often the real arrival falls inside the window) is measured by
tracking_report, not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.clock import SimTimeMs
from core.config import Settings
from core.general_city import GeneralCity


@dataclass(frozen=True)
class StopLineTarget:
    intersection_id: str
    route_index: int  # index into the route's node list
    s_m: float  # distance along the route to this stop line
    queue_wait_ms: int  # expected discharge wait ahead of the vehicle
    cycle_ms: int  # full signal cycle at this intersection
    expected_red_wait_ms: int = 0  # projected wait for this approach's next green


@dataclass(frozen=True)
class IntersectionEta:
    intersection_id: str
    eta_unimpeded_ms: SimTimeMs
    eta_expected_ms: SimTimeMs
    eta_low_ms: SimTimeMs
    eta_high_ms: SimTimeMs


def compute_etas(
    city: GeneralCity,
    settings: Settings,
    route: list[str],
    targets: list[StopLineTarget],
    s_m: float,
    speed_std_mps: float,
    gps_age_ms: int,
    stale_fraction: float,
    now: SimTimeMs,
) -> list[IntersectionEta]:
    route_segments = [f"{route[i]}->{route[i + 1]}" for i in range(len(route) - 1)]
    cumulative = _cumulative_lengths(city, route_segments)

    results: list[IntersectionEta] = []
    for target in targets:
        if target.s_m < s_m:
            continue  # already behind us
        travel_s = _travel_seconds(city, settings, route_segments, cumulative, s_m, target.s_m)
        unimpeded_ms = now + round(travel_s * 1000)

        # Departure cannot happen before the vehicle arrives or before this
        # approach next turns green (red_wait is measured from now), whichever is
        # later; the queue then discharges ahead of it.
        green_at_ms = now + target.expected_red_wait_ms
        expected_ms = max(unimpeded_ms, green_at_ms) + target.queue_wait_ms

        half_width_ms = round(
            settings.eta_base_window_ms
            + settings.eta_k_gps * gps_age_ms
            + settings.eta_k_speed * speed_std_mps * travel_s * 1000
            + settings.eta_k_stale * stale_fraction * (expected_ms - now)
            + settings.eta_k_wait * target.expected_red_wait_ms
        )

        centre_low = min(unimpeded_ms, expected_ms)
        centre_high = max(unimpeded_ms, expected_ms)
        results.append(
            IntersectionEta(
                intersection_id=target.intersection_id,
                eta_unimpeded_ms=unimpeded_ms,
                eta_expected_ms=expected_ms,
                eta_low_ms=max(now, centre_low - half_width_ms),
                eta_high_ms=centre_high + half_width_ms,
            )
        )
    return results


def _cumulative_lengths(city: GeneralCity, route_segments: list[str]) -> list[float]:
    total = 0.0
    out = [0.0]
    for sid in route_segments:
        total += city.segment(sid).length_m
        out.append(total)
    return out


def _travel_seconds(
    city: GeneralCity,
    settings: Settings,
    route_segments: list[str],
    cumulative: list[float],
    from_s: float,
    to_s: float,
) -> float:
    """Integrates time over route segments between two route positions, each
    segment at its cruise speed (free-flow times the ambulance factor)."""
    seconds = 0.0
    for i, sid in enumerate(route_segments):
        seg_start, seg_end = cumulative[i], cumulative[i + 1]
        lo, hi = max(from_s, seg_start), min(to_s, seg_end)
        if hi <= lo:
            continue
        speed = city.segment(sid).free_flow_speed_mps * settings.ambulance_speed_factor
        seconds += (hi - lo) / speed
    return seconds
