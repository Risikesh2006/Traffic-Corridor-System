# Product Requirements Document (PRD)
# Emergency Traffic Corridor — Real-Time Ambulance Signal Control System

| Field | Value |
|---|---|
| Document type | Product Requirements Document |
| Product name | Corridor Control |
| Problem statement | PS 1 — Smart City: Emergency Traffic Corridor |
| Context | Advanced System Design Challenge (internship) |
| Version | 1.0 |
| Status | Approved for build |

---

## Table of Contents

1. Product Summary
2. Problem Statement
3. Goals and Non-Goals
4. Users and Personas
5. User Stories
6. Scope and Release Tiers
7. Domain Background
8. System Architecture
9. Domain Model
10. Functional Requirements
11. Algorithms Specification
12. Safety Invariants
13. Failure Handling Requirements
14. Non-Functional Requirements
15. API Specification
16. Real-Time (WebSocket) Specification
17. User Interface Requirements
18. Configuration Parameters
19. Testing Strategy and Acceptance Criteria
20. Success Metrics
21. Tech Stack and Repository Structure
22. Delivery Phases
23. Risks and Mitigations
24. Assumptions and Limitations
25. Future Work
26. Glossary

---

## 1. Product Summary

Corridor Control is a real-time traffic control system that creates an emergency corridor for an ambulance travelling through a congested city. It identifies where the ambulance is, chooses the best route, and changes traffic signals ahead of it at the right moment so that queues clear before it arrives. It keeps adapting when roads close, when traffic changes, and when data or parts of the system fail.

The product is built as a **digital twin**: real control software and real users (operators, ambulance crews, hospital staff) working on a simulated city. Only the physical world (roads, vehicles, signal hardware, sensors) is simulated. All decision logic, safety enforcement, failure handling and user interfaces are real.

**Design principle:** *The central system decides priority. Each intersection guarantees its own safety.* A crash, network loss or bad data may make the ambulance slower, but can never make an intersection unsafe.

**One-line pitch:** A safety-first emergency corridor controller — real control logic and real users on a simulated city, proven safe across thousands of randomised failure scenarios, with every decision explainable.

---

## 2. Problem Statement

### 2.1 The problem
An ambulance carrying a critical patient must cross a city during peak traffic while congestion and a road accident affect the network. Red signals and queued vehicles delay it at every junction. Delays during this period directly affect patient outcomes.

Turning signals green is not enough:
- Too early wastes green time and starves cross traffic.
- Too late means the queue has not cleared and the ambulance still stops.
- The best route depends on signal and queue state, and the signal plan depends on the route.
- Roads can close mid-trip, data can be stale, and parts of the system can fail.

### 2.2 Who experiences it

| Stakeholder | Need |
|---|---|
| Patient | Fastest possible arrival at hospital |
| Ambulance crew | A clear, predictable path; early notice of route changes |
| Traffic control operator | Visibility, control and trust in system decisions |
| Hospital staff | Accurate arrival time to prepare |
| Cross-street drivers and pedestrians | Safety, and bounded waiting time |

### 2.3 What makes it difficult
1. **Moving target with uncertain timing.** ETAs change continuously, but signal changes take several seconds to execute safely, so the system must act on predictions.
2. **Routing and signal control are coupled.**
3. **Local optimisation causes global harm.** Holding a corridor green pushes queues onto neighbouring roads, which can spill back into the corridor.
4. **Data is never perfectly fresh.**
5. **Safety and speed conflict.** The fastest action (switch to green now) is the unsafe one.

---

## 3. Goals and Non-Goals

### 3.1 Goals
| ID | Goal |
|---|---|
| G1 | Minimise ambulance travel time and number of stops from entry to hospital |
| G2 | Never produce a conflicting or unsafe signal state, under any failure |
| G3 | Recalculate route and signal strategy on a mid-route road closure without restarting |
| G4 | Continue operating safely with missing or stale sensor data |
| G5 | Make decisions within defined latency targets |
| G6 | Bound the impact on cross traffic (max hold, recovery) |
| G7 | Make every decision explainable and traceable |
| G8 | Provide usable interfaces for operators, ambulance crews and hospitals |

### 3.2 Non-Goals
- Controlling real traffic hardware in this version.
- Vehicle-level microscopic traffic simulation (queue-level model is sufficient).
- Machine-learned signal control (reinforcement learning) in the safety-critical path.
- Pedestrian-level simulation beyond respecting pedestrian clearance intervals.
- Public-facing app for general drivers.
- Production-grade multi-city scaling (designed for, not built).

---

## 4. Users and Personas

### 4.1 Traffic Control Operator (primary user)
- Works in the city traffic control room.
- Monitors the network, reports incidents, closes and reopens roads, overrides decisions, monitors system health.
- Needs: clear situational awareness, trust in automated decisions, ability to intervene.

### 4.2 Ambulance Crew
- Driver and paramedic in the vehicle.
- Starts a trip, follows route guidance, receives reroute alerts.
- Needs: large, glanceable instructions; no distraction; immediate notice of route changes with a reason.

### 4.3 Hospital Staff
- Emergency department coordinator.
- Sees incoming ambulances, priority and live ETA.
- Needs: accurate, updating arrival times.

### 4.4 Reviewer / Evaluator (demo persona)
- Runs scenarios, injects failures, inspects decisions and test evidence.
- Needs: one-command setup, scenario controls, replay, clear documentation.

---

## 5. User Stories

### Operator
| ID | Story | Priority |
|---|---|---|
| US-O1 | As an operator, I want to see the live map with congestion, signal states, the ambulance and its corridor, so I understand the situation at a glance. | Must |
| US-O2 | As an operator, I want to dispatch an ambulance to a hospital, so the system starts creating a corridor. | Must |
| US-O3 | As an operator, I want to report an accident and close a road, so the system reroutes around it. | Must |
| US-O4 | As an operator, I want to see why each signal decision was made, so I can trust and verify the system. | Must |
| US-O5 | As an operator, I want to see system health and the current operating mode, so I know when data or components are degraded. | Must |
| US-O6 | As an operator, I want to release all preemptions or pause the corridor, so I can intervene in an emergency. | Must |
| US-O7 | As an operator, I want to inspect a single intersection's phase timeline, lease and safety checks. | Should |
| US-O8 | As an operator, I want to reopen a closed road. | Must |
| US-O9 | As an operator, I want to replay a past run step by step. | Should |
| US-O10 | As an operator, I want to compare a run with a no-system baseline. | Should |

### Ambulance crew
| ID | Story | Priority |
|---|---|---|
| US-C1 | As a crew member, I want to start a trip to a hospital from my phone. | Must |
| US-C2 | As a crew member, I want to see the next turn and ETA in large text. | Must |
| US-C3 | As a crew member, I want to see which upcoming signals are already green for me. | Should |
| US-C4 | As a crew member, I want a clear alert with a reason when my route changes. | Must |
| US-C5 | As a crew member, I want the option to use my phone's real GPS. | Could |

### Hospital
| ID | Story | Priority |
|---|---|---|
| US-H1 | As hospital staff, I want to see incoming ambulances with priority and live ETA. | Must |
| US-H2 | As hospital staff, I want to be alerted when an ETA changes significantly. | Should |

### Reviewer
| ID | Story | Priority |
|---|---|---|
| US-R1 | As a reviewer, I want to inject failures with one click and watch the response. | Must |
| US-R2 | As a reviewer, I want to run the same scenario again with the same seed and get the same result. | Must |
| US-R3 | As a reviewer, I want a chaos report showing safety across many randomised runs. | Should |

---

## 6. Scope and Release Tiers

| Tier | Contents | Purpose |
|---|---|---|
| **Tier 1 — Minimum (MVP)** | Simulation, safety layer, world state, tracking, routing, corridor control | Complete safe working core |
| **Tier 2 — Strong** | Dynamic events, resilience, event store, API with roles, operator dashboard, crew app, hospital screen | Full product |
| **Tier 3 — Exceptional** | Deterministic chaos testing, baseline metrics, latency tracing, replay | Evidence and differentiation |

---

## 7. Domain Background

These real-world rules shape the requirements:

- **Phases.** An intersection runs phases: sets of non-conflicting movements that are green together (e.g., North–South through).
- **Clearance intervals.** A green cannot become red instantly. It must go GREEN → AMBER (3–4 s) → ALL-RED (1–2 s) before a conflicting movement gets green.
- **Minimum green.** Every phase runs for a minimum time once started.
- **Pedestrian clearance.** Pedestrians who have started crossing must be given time to finish.
- **Conflict matrix.** Defines which movements may never be green at the same time. Real signal cabinets include a hardware conflict monitor that forces flashing red if a conflict ever occurs.
- **Queue discharge.** Queued vehicles leave at roughly one vehicle every 2 s per lane after about 2 s of startup loss. A 15-vehicle queue needs about 30+ s to clear.
- **Emergency preemption.** Real systems request preemption, safely terminate the current phase, give green to the emergency approach, then recover to normal operation.

---

## 8. System Architecture

### 8.1 Style
Hierarchical, event-driven modular monolith with an independent safety layer.

- **Hierarchical:** central planner requests; local intersection controllers own their signal state and can always run alone.
- **Event-driven:** incidents and state changes trigger immediate reaction; all events are logged for replay.
- **Modular monolith:** one deployable process with clean module boundaries. Avoids network hops and new failure modes; modules can be split into services later.
- **Independent safety supervisor:** small, rule-only, with veto power over every command.

### 8.2 Component diagram

```
 Ambulance GPS ─┐                         ┌───────────────────────────────┐
 Road sensors ──┼─► [1 Ingestion Gateway] │ Operator / Crew / Hospital UIs │
 Incidents ─────┘          │              └──────────────▲────────────────┘
                           ▼                             │ WebSocket + REST
                  ┌─────────────────┐                    │
                  │   EVENT BUS     │◄───── [10 Health Monitor]
                  └──┬──────┬───────┴──┐
                     ▼      ▼          ▼
         [2 World State] [3 Vehicle Tracker] [9 Event Store]
                     │      │
                     ▼      ▼
              [4 Route Planner]
                     │
                     ▼
              [5 Corridor Planner + 6 Neighbour Coordination]
                     │ proposed commands
                     ▼
              [7 SAFETY SUPERVISOR] (veto)
                     │ validated, leased, versioned commands
                     ▼ (via SignalLink / SimNetwork)
              [8 Intersection Controllers × N]
                 own state machine, local safety check,
                 run normal plan by default
```

### 8.3 Components and responsibilities

| # | Component | Responsibility |
|---|---|---|
| 1 | Ingestion Gateway | Validate, timestamp and deduplicate inputs; reject impossible readings |
| 2 | World State | Road graph, live density with age and confidence, historical profiles |
| 3 | Vehicle Tracker | Smooth GPS, map-match, predict ETAs, dead-reckon, detect deviation |
| 4 | Route Planner | A* on live cost graph, backup routes, hysteresis, replanning |
| 5 | Corridor Planner | Compute preemption windows for intersections in the lookahead horizon |
| 6 | Neighbour Coordination | Inflow gating, downstream flush, max hold, recovery |
| 7 | Safety Supervisor | Validate every command against conflict matrix and timing rules |
| 8 | Intersection Controller | Single writer of its signal state; executes leases; second safety check |
| 9 | Event Store | Append-only log of all inputs, decisions and commands, with causation IDs |
| 10 | Health Monitor | Heartbeats, operating mode (NORMAL / DEGRADED / SAFE) |
| 11 | API Layer | REST, WebSocket, authentication, role-based access |
| 12 | Chaos Module | Failure injection, invariant checking, chaos runner |
| 13 | Simulator | City, traffic, simulated ambulance, sensors and network |

### 8.4 Hardware abstraction boundary
The control system interacts with the physical world only through these interfaces:

| Interface | Simulated by | Real-world equivalent |
|---|---|---|
| `VehicleFeed` | Simulated ambulance | GPS unit / crew phone over 4G |
| `SensorFeed` | Simulated detectors | Loop detectors, cameras, probe data |
| `SignalLink` | `SimNetwork` + simulated controllers | Field controllers via NTCIP |

Control logic must not import simulator modules directly.

### 8.5 Data flow
1. Inputs arrive at the Ingestion Gateway, are validated and published as events.
2. World State and Vehicle Tracker update.
3. Route Planner replans on trigger events or periodic timer.
4. Corridor Planner recomputes preemption windows every tick.
5. Proposed commands go to the Safety Supervisor.
6. Approved commands are sent with lease, version and idempotency key to controllers.
7. Controllers acknowledge; state changes are published.
8. All events are written to the Event Store and pushed to UIs over WebSocket.

### 8.6 Control loop timing
- **Corridor tick:** every 1 s (simulated time).
- **Replan triggers:** road closure, route deviation, ETA drift > 15%, intersection offline, hospital unavailable; plus periodic replan every 10 s.
- **Simulation tick:** 10 internal ticks per simulated second.

### 8.7 Determinism rules
- All time comes from `SimClock`. No direct `time.time()` in system code.
- All randomness comes from a seeded `random.Random(seed)`.
- Given the same seed and inputs, a run must be exactly reproducible.

---

## 9. Domain Model

| Entity | Key fields |
|---|---|
| `Node` / `Intersection` | id, position (x, y), approaches, phases, conflict_matrix, controller_status (ONLINE / UNREACHABLE / FLASHING), current_phase, phase_started_at |
| `Approach` | id, intersection_id, direction (N/S/E/W), lanes, queue_length |
| `Phase` | id, movements[], min_green_s, amber_s, all_red_s, ped_clearance_s |
| `ConflictMatrix` | movements[], conflicts: set of (movement, movement) |
| `RoadSegment` | id, from_node, to_node, length_m, lanes, free_flow_speed_mps, capacity_vph, status (OPEN / CLOSED), density_estimate, confidence |
| `SensorReading` | source_id, road_id, value, timestamp, sequence, confidence |
| `EmergencyVehicle` | id, priority (P1–P3), position, speed, heading, destination_id, last_seen_at, position_source (GPS / DEAD_RECKONING) |
| `Route` | id, version, vehicle_id, edges[], cost_s, computed_at, alternatives[] |
| `CorridorPlan` | route_version, entries[]: {intersection_id, phase_id, eta, request_at, hold_until, status} |
| `PreemptionLease` | lease_id, intersection_id, phase_id, plan_version, granted_at, expires_at, idempotency_key, status (ACTIVE / RELEASED / EXPIRED) |
| `Command` | id, type (PREEMPT / RENEW / RELEASE / GATE / RECOVER), intersection_id, plan_version, idempotency_key, payload, created_at |
| `Incident` | id, type, affected_edges[], severity, reported_at, cleared_at |
| `Hospital` | id, node_id, name, status (AVAILABLE / UNAVAILABLE) |
| `Event` | id, type, timestamp, payload, causation_id, correlation_id |
| `User` | id, name, role (OPERATOR / CREW / HOSPITAL), assigned_vehicle_id / hospital_id |

### 9.1 Intersection controller state machine
```
GREEN(phase) --[min green met & change requested]--> AMBER
AMBER --[amber_s elapsed]--> ALL_RED
ALL_RED --[all_red_s elapsed]--> GREEN(next phase)
GREEN(preempted phase) --[lease active]--> hold
any --[lease expired/released]--> transition to normal plan via AMBER/ALL_RED
any --[conflict detected]--> FLASHING_RED (fail-safe)
```

### 9.2 Lease state machine
```
REQUESTED --[supervisor approves, controller acks]--> ACTIVE
ACTIVE --[renewed]--> ACTIVE (expires_at extended)
ACTIVE --[ambulance passed / route changed / operator release]--> RELEASED
ACTIVE --[expires_at reached without renewal]--> EXPIRED
REQUESTED --[supervisor rejects]--> REJECTED
```

### 9.3 Operating mode state machine
```
NORMAL --[stale data / unreachable controller / GPS lost]--> DEGRADED
DEGRADED --[all conditions recovered]--> NORMAL
NORMAL/DEGRADED --[safety supervisor down / planner unrecoverable]--> SAFE
SAFE --[components healthy again]--> DEGRADED --> NORMAL
```

---

## 10. Functional Requirements

### 10.1 Simulation (FR-SIM)
| ID | Requirement |
|---|---|
| FR-SIM-01 | Load the city (nodes, roads, phases, conflict matrices, hospitals) from a JSON config file. |
| FR-SIM-02 | Default city: 6×6 grid with 2–3 diagonal arterials and at least 1 hospital. |
| FR-SIM-03 | Simulate vehicle arrivals per approach using a time-of-day demand profile with peak conditions. |
| FR-SIM-04 | Simulate queue discharge at ~1 vehicle per 2 s per lane during green, with ~2 s startup loss. |
| FR-SIM-05 | Simulate the ambulance moving along its assigned route at traffic-dependent speed; it must stop at red and slow in queues. |
| FR-SIM-06 | Simulate density sensors reporting periodically with configurable noise. |
| FR-SIM-07 | Simulate the network between central and intersections, supporting configurable delay, drop, duplication and reordering. |
| FR-SIM-08 | Support simulation speed multipliers (0.5x, 1x, 2x, 5x) and pause. |
| FR-SIM-09 | Every run is reproducible from its seed. |

### 10.2 Ingestion (FR-ING)
| ID | Requirement |
|---|---|
| FR-ING-01 | Attach receive timestamp and validate schema of every input. |
| FR-ING-02 | Reject GPS readings implying speed above a physical maximum (e.g., 50 m/s). |
| FR-ING-03 | Deduplicate readings by (source_id, sequence). |
| FR-ING-04 | Discard readings older than the latest already accepted from the same source. |
| FR-ING-05 | Publish accepted inputs as events. |

### 10.3 World State (FR-WS)
| ID | Requirement |
|---|---|
| FR-WS-01 | Maintain density estimate, age and confidence per road. |
| FR-WS-02 | Compute confidence as `e^(−age/τ)`. |
| FR-WS-03 | Blend live value with historical profile: `estimate = c × live + (1 − c) × historical`. |
| FR-WS-04 | Beyond hard TTL, ignore live data, use historical profile and apply a pessimistic penalty. |
| FR-WS-05 | Track road status (OPEN / CLOSED) and intersection controller status. |
| FR-WS-06 | Never treat missing data as "empty road". |

### 10.4 Vehicle Tracking (FR-TRK)
| ID | Requirement |
|---|---|
| FR-TRK-01 | Smooth GPS with an exponentially weighted moving average. |
| FR-TRK-02 | Map-match position to the nearest road segment on or near the route. |
| FR-TRK-03 | Predict ETA at each upcoming intersection with a confidence window. |
| FR-TRK-04 | On GPS loss (> 3 s), dead-reckon along the route at last known speed and widen the ETA window. |
| FR-TRK-05 | Detect route deviation and publish a deviation event. |
| FR-TRK-06 | Detect stop-line passage at each intersection. |

### 10.5 Routing (FR-RT)
| ID | Requirement |
|---|---|
| FR-RT-01 | Compute routes with A* using an admissible straight-line heuristic. |
| FR-RT-02 | Edge cost = BPR travel time + queue clearance penalty + stale-data penalty + uncontrollable-signal penalty; closed edges = ∞. |
| FR-RT-03 | Compute k = 3 alternative routes (Yen's algorithm) in the background. |
| FR-RT-04 | Apply hysteresis: adopt a new route only if better by > 15 s or > 10%. |
| FR-RT-05 | Replan from the vehicle's current position, not its origin. |
| FR-RT-06 | Version every route; versions increase monotonically. |
| FR-RT-07 | Reroute to the next suitable hospital if the destination becomes unavailable. |

### 10.6 Corridor Control (FR-COR)
| ID | Requirement |
|---|---|
| FR-COR-01 | For each intersection within the lookahead horizon (default 90 s), compute request time = ETA − (queue × headway + startup loss + transition time + safety buffer). |
| FR-COR-02 | Increase the safety buffer as ETA confidence decreases. |
| FR-COR-03 | Issue preemption as a time-limited lease (default 30 s), renewed while still needed. |
| FR-COR-04 | Release the lease when the ambulance passes the stop line, the route changes, or the operator releases it. |
| FR-COR-05 | Attach plan version and idempotency key to every command. |
| FR-COR-06 | Apply inflow gating to side streets feeding the corridor. |
| FR-COR-07 | Apply downstream flush to intersections beyond the next. |
| FR-COR-08 | Never hold any approach red beyond max hold (default 90 s); serve it once, then resume preemption. |
| FR-COR-09 | After passage, recover to the normal plan over 1–2 cycles with compensation green for starved approaches. |
| FR-COR-10 | Produce a human-readable explanation for every decision. |

### 10.7 Dynamic Events (FR-DYN)
| ID | Requirement |
|---|---|
| FR-DYN-01 | On road closure affecting the route: set cost to ∞, switch to a backup route immediately, run a full replan, release leases on the abandoned path, schedule leases on the new path — without restarting any component. |
| FR-DYN-02 | Handle route deviation by replanning from actual position. |
| FR-DYN-03 | Handle a stopped ambulance without holding signals beyond max hold. |
| FR-DYN-04 | Treat offline or flashing intersections as uncontrollable with a cost penalty. |
| FR-DYN-05 | Handle two ambulances: higher priority first; equal priority → earlier ETA first; the second gets the next window. |
| FR-DYN-06 | Support reopening a closed road, triggering a replan. |

### 10.8 Safety (FR-SAF)
| ID | Requirement |
|---|---|
| FR-SAF-01 | Validate every command against the conflict matrix and all timing rules before sending. |
| FR-SAF-02 | Reject unsafe commands with a logged reason. |
| FR-SAF-03 | Re-validate inside each intersection controller before execution. |
| FR-SAF-04 | Fail closed: if the supervisor is unavailable, no commands are executed. |
| FR-SAF-05 | If a controller ever detects a conflict, enter FLASHING_RED. |

### 10.9 Resilience (FR-RES)
| ID | Requirement |
|---|---|
| FR-RES-01 | Every module and intersection sends heartbeats. |
| FR-RES-02 | Health Monitor switches operating mode automatically and back on recovery. |
| FR-RES-03 | Append every input, decision and command to the Event Store with causation IDs. |
| FR-RES-04 | A restarted module rebuilds its state from the Event Store. |
| FR-RES-05 | Controllers ignore commands with a plan version older than their current one. |
| FR-RES-06 | Controllers acknowledge but do not re-execute duplicate idempotency keys. |
| FR-RES-07 | On lease expiry, controllers return to the normal plan without central involvement. |

### 10.10 Users and Access (FR-USR)
| ID | Requirement |
|---|---|
| FR-USR-01 | Authenticate users with JWT. |
| FR-USR-02 | Enforce roles: OPERATOR (all), CREW (own trip), HOSPITAL (own arrivals). |
| FR-USR-03 | Accept preemption-triggering trips only from an authenticated crew with an active dispatch. |
| FR-USR-04 | Mutating API requests accept an idempotency key; repeated keys return the original result. |

### 10.11 Chaos and Evidence (FR-CHA)
| ID | Requirement |
|---|---|
| FR-CHA-01 | Provide one failure injection per failure type (Section 13). |
| FR-CHA-02 | Check all safety invariants (Section 12) after every simulation tick. |
| FR-CHA-03 | Run N randomised headless scenarios with random seeds, failures and timings. |
| FR-CHA-04 | Save the seed of any failing run for exact replay. |
| FR-CHA-05 | Produce a chaos report: runs, violations, arrival rate, average time saved, worst-case hold. |

### 10.12 Metrics and Replay (FR-MET)
| ID | Requirement |
|---|---|
| FR-MET-01 | Run a baseline (system off) with the same seed and compare travel time, stops and cross-street delay. |
| FR-MET-02 | Record per-stage latency for every decision and report p50 / p95. |
| FR-MET-03 | Replay any run from the Event Store, step by step, with cause-and-effect chains. |

---

## 11. Algorithms Specification

### 11.1 Travel time (BPR function)
```
t = t_free × (1 + α × (v / c)^β)      α = 0.15, β = 4
t_free = length / free_flow_speed
v = estimated volume, c = capacity
```

### 11.2 Edge cost for routing
```
cost(edge) = t_bpr(edge)
           + queue_clear_time(next_intersection)
           + stale_penalty(edge)          # grows as confidence drops
           + uncontrollable_penalty(node) # e.g. +45 s if offline/flashing
cost(edge) = ∞ if edge.status == CLOSED
```

### 11.3 A* search
- Heuristic: straight-line distance / maximum speed (admissible).
- Priority queue: binary heap.
- Replan from current map-matched position.

### 11.4 Backup routes
- Yen's k-shortest loopless paths, k = 3, recomputed in the background after each replan.

### 11.5 Route hysteresis
```
adopt new route if (old_cost − new_cost) > max(15 s, 0.10 × old_cost)
or old route is blocked
```

### 11.6 Preemption timing
```
queue_clear  = queue_length × headway (2 s) + startup_loss (2 s)
transition   = remaining_min_green + amber + all_red (+ ped clearance if active)
buffer       = base_buffer (3 s) + k × eta_uncertainty
request_time = ETA − (queue_clear + transition + buffer)
hold_until   = stop-line passage OR lease expiry
```

### 11.7 Stale data blending
```
confidence = e^(−age / τ)       τ_density = 30 s, τ_gps = 5 s
estimate   = confidence × live + (1 − confidence) × historical(time_of_day)
if age > hard_ttl: estimate = historical × pessimism_factor (1.2)
```

### 11.8 ETA prediction
- Speed estimate = EWMA of observed speed, capped by segment speed estimate.
- ETA to intersection i = remaining distance on current edge / speed + Σ edge travel times to i.
- Confidence window grows with GPS age and data staleness.

### 11.9 Multi-ambulance conflict resolution
```
if windows overlap at an intersection:
    winner = higher priority; tie → earlier ETA
    loser window starts after winner passage + clearance
```

### 11.10 Machine learning position
No learned model is used in the safety-critical path. Historical profiles and EWMA are used for estimation. Rationale: explainability, verifiability and no training-data requirement. An ETA regression model is listed as future work, outside the safety path.

---

## 12. Safety Invariants

These must hold at every simulation tick. They are checked by the invariant checker (FR-CHA-02) and property tests.

| ID | Invariant |
|---|---|
| INV-01 | No two conflicting movements are green at the same intersection at the same time. |
| INV-02 | No amber interval is shorter than configured amber time. |
| INV-03 | No all-red interval is shorter than configured all-red time. |
| INV-04 | No phase ends before its minimum green. |
| INV-05 | Active pedestrian clearance is never cut short. |
| INV-06 | No approach is held red longer than max hold. |
| INV-07 | No lease remains active on an intersection off the current route after the grace period. |
| INV-08 | A controller's applied plan version never decreases. |
| INV-09 | No idempotency key is executed more than once. |
| INV-10 | No command is executed without Safety Supervisor approval. |

---

## 13. Failure Handling Requirements

| ID | Failure | Detection | Required response | Expected outcome |
|---|---|---|---|---|
| FH-01 | Density data missing or stale | Age > TTL | Historical profile + pessimistic penalty; mark road low-confidence in UI | Conservative but valid routing |
| FH-02 | Ambulance GPS lost | No update > 3 s | Dead reckoning; widen ETA window; extend holds; mode → DEGRADED | Corridor stays open |
| FH-03 | Impossible GPS jump | Speed validation | Reject reading; keep estimate; log | Bad data ignored |
| FH-04 | Route planner crash | Missed heartbeat | Existing leases continue; restart planner; rebuild from Event Store | No interruption for ambulance |
| FH-05 | Safety supervisor crash | Missed heartbeat | Fail closed; no new commands; leases expire; mode → SAFE | Safe, never unsafe |
| FH-06 | Network loss to intersection | No ack / heartbeat | Lease expires; controller runs local plan; marked uncontrollable; route penalised | Routed around |
| FH-07 | Duplicate commands | Idempotency key | Acknowledge, don't re-execute | No double action |
| FH-08 | Out-of-order old command | Plan version | Ignore older version | No rollback |
| FH-09 | Intersection power failure / flashing | Controller status | Treat as all-way stop with high cost | Avoided if possible |
| FH-10 | Road closure on route | Incident event | Section FR-DYN-01 | Rerouted < 500 ms |
| FH-11 | Ambulance deviates | Map-matching | Replan from actual position; release old leases | Follows driver |
| FH-12 | Ambulance stopped | Speed ≈ 0 for > 10 s | Max hold applies; resume when moving | Cross traffic not starved |
| FH-13 | Two ambulances, same junction | Overlapping windows | Section 11.9 | Deterministic sequencing |
| FH-14 | Hospital unavailable | Hospital status event | Reroute to next suitable hospital | Patient still delivered |
| FH-15 | UI WebSocket disconnect | Socket close | Auto-reconnect with backoff; "data as of" banner; keep last state | UI never blanks |
| FH-16 | UI component error | Error boundary | Fallback for that panel only | Rest of UI works |
| FH-17 | Duplicate API request (e.g. double-click dispatch) | API idempotency key | Return original result | One trip created |

---

## 14. Non-Functional Requirements

### 14.1 Performance (latency targets, p95)
| Operation | Target |
|---|---|
| Input → state updated | < 50 ms |
| Corridor tick (ETA + preemption update) | < 100 ms |
| Full replan | < 300 ms |
| Road closure → new commands sent | < 500 ms |
| Safety validation per command | < 5 ms |
| State change → UI update | < 250 ms |

Rationale: physical signal transitions take 4–6 s; prediction and lead time matter more than raw compute speed, but compute must stay well inside the transition budget.

### 14.2 Safety
- Zero violations of Section 12 invariants under all tests and chaos runs.

### 14.3 Reliability
- No single component failure may produce an unsafe state.
- Any single module crash recovers automatically.

### 14.4 Determinism
- Identical seed and inputs produce identical event logs.

### 14.5 Security
- All user endpoints require authentication; role checks on every endpoint.
- Preemption only for authenticated crews with active dispatch.
- Secrets in environment variables, not in code.

### 14.6 Usability
- Operator understands the situation within 5 seconds of opening Live Control.
- Crew app readable at arm's length: next instruction ≥ 28 px; touch targets ≥ 44 px.
- Every decision has a plain-language explanation.

### 14.7 Maintainability
- Modules communicate through interfaces and events.
- Control code never imports simulator code.
- Linting (ruff) and type hints across the backend.

### 14.8 Portability
- `docker compose up` starts the full system.

---

## 15. API Specification

Base path: `/api/v1`. All endpoints require `Authorization: Bearer <jwt>` except login. Mutating endpoints accept `Idempotency-Key` header.

### 15.1 Auth
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/auth/login` | — | Returns JWT and role |

### 15.2 Simulation and scenarios
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/sim/start` | OPERATOR | Start with `{seed, scenario, speed}` |
| POST | `/sim/pause` | OPERATOR | Pause |
| POST | `/sim/resume` | OPERATOR | Resume |
| POST | `/sim/speed` | OPERATOR | Set `{multiplier}` |
| POST | `/sim/reset` | OPERATOR | Reset to initial state |
| GET | `/sim/state` | ANY | Full current snapshot |

### 15.3 Dispatch and trips
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/trips` | OPERATOR, CREW | Create trip `{vehicle_id, hospital_id, priority}` |
| GET | `/trips/{id}` | OPERATOR, assigned CREW | Trip details, route, ETAs |
| POST | `/trips/{id}/cancel` | OPERATOR, assigned CREW | Cancel and release leases |
| POST | `/vehicles/{id}/gps` | CREW | Push real GPS `{lat, lng, accuracy, seq, ts}` |

### 15.4 Incidents and roads
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/incidents` | OPERATOR | Report `{type, edge_ids[], severity}` |
| POST | `/incidents/{id}/clear` | OPERATOR | Clear and reopen roads |
| GET | `/incidents` | OPERATOR | List active incidents |

### 15.5 Corridor and intersections
| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/corridor` | OPERATOR | Current corridor plan |
| POST | `/corridor/release-all` | OPERATOR | Release all leases |
| POST | `/corridor/pause` | OPERATOR | Stop issuing new preemptions |
| GET | `/intersections` | OPERATOR | List with status |
| GET | `/intersections/{id}` | OPERATOR | Detail: phases, lease, checks, history |

### 15.6 Health, metrics, decisions
| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/health` | ANY | Mode and component status |
| GET | `/metrics/latency` | OPERATOR | p50 / p95 per stage |
| GET | `/metrics/impact?run_id=` | OPERATOR | Run vs baseline |
| GET | `/decisions?since=` | OPERATOR | Decision log entries |

### 15.7 Failure injection
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/chaos/inject` | OPERATOR | `{type, target, duration_s}` |
| POST | `/chaos/restore` | OPERATOR | `{type, target}` |
| POST | `/chaos/run` | OPERATOR | Start chaos batch `{runs, workers}` |
| GET | `/chaos/report/{batch_id}` | OPERATOR | Chaos report |

Injection types: `STALE_SENSORS`, `GPS_LOSS`, `INTERSECTION_DISCONNECT`, `DUPLICATE_FLOOD`, `STALE_COMMAND`, `PLANNER_CRASH`, `SUPERVISOR_CRASH`, `SIGNAL_POWER_FAILURE`, `ROAD_CLOSURE`, `HOSPITAL_UNAVAILABLE`.

### 15.8 Replay
| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/runs` | OPERATOR | List recorded runs |
| GET | `/runs/{id}/events?from=&to=` | OPERATOR | Event range |
| GET | `/runs/{id}/snapshot?at=` | OPERATOR | Reconstructed state at time |

### 15.9 Hospital
| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/hospitals/{id}/arrivals` | HOSPITAL | Incoming ambulances with ETA |
| POST | `/hospitals/{id}/status` | OPERATOR | Set AVAILABLE / UNAVAILABLE |

### 15.10 Error format
```json
{ "error": { "code": "LEASE_REJECTED", "message": "Would shorten amber", "details": {} } }
```

---

## 16. Real-Time (WebSocket) Specification

Endpoint: `/ws?token=<jwt>`. Server filters messages by role.

### 16.1 Message envelope
```json
{ "type": "STATE_DIFF", "seq": 18402, "sim_time": "14:02:31.000", "payload": { } }
```

### 16.2 Message types
| Type | Sent to | Payload |
|---|---|---|
| `SNAPSHOT` | On connect | Full state |
| `STATE_DIFF` | Every tick | Changed signals, queues, vehicle positions |
| `ROUTE_UPDATED` | Operator, crew | New route, version, reason |
| `DECISION` | Operator | Decision log entry with explanation |
| `INCIDENT` | Operator, crew | Incident created/cleared |
| `MODE_CHANGED` | All | NORMAL / DEGRADED / SAFE |
| `HEALTH` | Operator | Component status |
| `ETA_UPDATE` | Crew, hospital | ETA and confidence |
| `ALERT` | Role-specific | Reroute, ETA change, failure alerts |

### 16.3 Client behaviour
- Track `seq`; on gap, request a fresh `SNAPSHOT`.
- Reconnect with exponential backoff (1 s, 2 s, 4 s … max 30 s).
- Show "data as of HH:MM:SS" when no message received for > 3 s.

---

## 17. User Interface Requirements

### 17.1 Global
- Dark control-room theme; colour never the only carrier of meaning (labels on all states).
- Top bar: product name, navigation, operating mode badge, simulation clock, connection status.
- Error boundary per panel.

### 17.2 Operator — Live Control
- Mission panel: vehicle ID, priority, ETA with confidence, destination, position, route version, stops.
- Corridor-ahead list: next 5 intersections with ETA, status (GREEN HELD / CLEARING / SCHEDULED / PLANNED / BEYOND HORIZON), queue progress.
- Map: roads coloured by congestion, signal states, ambulance, route (travelled and remaining), preempted signals ringed, hospitals marked, closed roads marked, stale roads dashed, offline signals flagged, position uncertainty shown in degraded mode.
- Decision log ("why") with timestamp, action type, intersection, explanation.
- Health summary with latency vs targets.
- Actions: pause corridor, release all preemptions, report incident (click a road).
- Incident state: red banner with reroute time; replan timeline; old vs new route.
- Degraded state: amber banner; active issues with detection and response.

### 17.3 Operator — Intersection Detail
- Searchable list; phase timeline (last 100 s); approach queues; lease card; safety checks; conflict matrix; command history (accepted, duplicate, ignored, rejected); recovery plan.

### 17.4 Operator — System Health
- Mode indicator (NORMAL / DEGRADED / SAFE) with descriptions; latency charts per stage with target lines; component heartbeat table; 36-intersection status grid; event store status.

### 17.5 Operator — Scenarios
- Presets (peak hour, dispatch ambulance, second ambulance); seed input; speed control; baseline run; reset.
- One card per failure injection: description, expected response, inject/restore button, active state.
- Chaos batch runner and report link.

### 17.6 Operator — Impact
- Travel time with system vs baseline; time saved; stops; added cross-street delay; per-section comparison; speed-over-trip chart; per-intersection hold and delay table.

### 17.7 Operator — Replay
- Timeline scrubber with event markers; step controls; event counter; map snapshot at selected time; cause-and-effect chain; selected event detail.

### 17.8 Crew App (mobile, 390 px wide)
- Login → assigned vehicle.
- Start trip: choose hospital, confirm.
- Navigation view: next instruction (large), distance, ETA, upcoming signals (green / scheduled), route mini-map.
- Reroute alert: full-width, high-contrast, with reason ("Road ahead closed — turn right at D4").
- GPS mode toggle: simulated / real phone GPS.
- Connection status indicator.

### 17.9 Hospital Arrivals
- List of incoming ambulances: vehicle, priority, ETA, confidence, status.
- Highlight ETA changes > 30 s.
- Mark hospital available / unavailable (operator only).

---

## 18. Configuration Parameters

| Parameter | Default | Description |
|---|---|---|
| `amber_s` | 4 | Amber interval |
| `all_red_s` | 2 | All-red clearance |
| `min_green_s` | 10 | Minimum green |
| `ped_clearance_s` | 7 | Pedestrian clearance |
| `max_hold_s` | 90 | Max red hold for any approach |
| `lease_duration_s` | 30 | Preemption lease length |
| `lookahead_horizon_s` | 90 | Corridor planning horizon |
| `headway_s` | 2.0 | Discharge headway per vehicle per lane |
| `startup_loss_s` | 2.0 | Queue startup loss |
| `base_buffer_s` | 3 | Preemption safety buffer |
| `tau_density_s` | 30 | Density confidence decay |
| `tau_gps_s` | 5 | GPS confidence decay |
| `density_ttl_s` | 60 | Hard TTL for density |
| `gps_loss_threshold_s` | 3 | GPS loss detection |
| `pessimism_factor` | 1.2 | Stale data penalty |
| `uncontrollable_penalty_s` | 45 | Cost for offline/flashing signals |
| `hysteresis_abs_s` | 15 | Route switch threshold (absolute) |
| `hysteresis_rel` | 0.10 | Route switch threshold (relative) |
| `k_alternatives` | 3 | Backup routes |
| `corridor_tick_s` | 1 | Corridor update interval |
| `periodic_replan_s` | 10 | Safety-net replan interval |
| `heartbeat_interval_s` | 1 | Heartbeat interval |
| `heartbeat_timeout_s` | 3 | Missed heartbeat threshold |
| `max_gps_speed_mps` | 50 | GPS validation limit |

---

## 19. Testing Strategy and Acceptance Criteria

### 19.1 Test levels
| Level | Tool | Scope |
|---|---|---|
| Unit | pytest | Cost model, A*, timing formulas, blending, state machines |
| Property-based | Hypothesis | Safety supervisor and controller invariants over random command sequences |
| Integration | pytest | Module chains (state → route → corridor → safety → controller) |
| Scenario / end-to-end | pytest + simulator | Full runs per scenario and failure type |
| Chaos | Chaos runner | Thousands of randomised runs with invariant checking |
| UI | Manual + basic component tests | Reconnect, error boundaries, role screens |

### 19.2 Key acceptance criteria
| ID | Criterion |
|---|---|
| AC-01 | Property tests pass on ≥ 10,000 random command sequences with zero invariant violations. |
| AC-02 | Same seed produces an identical event log on two runs. |
| AC-03 | In the reference scenario, the ambulance reaches the hospital with 0 stops at preempted intersections. |
| AC-04 | Mid-route road closure produces new commands in < 500 ms (p95) without restart. |
| AC-05 | No active lease remains on the abandoned route after the grace period. |
| AC-06 | No approach exceeds max hold in any test or chaos run. |
| AC-07 | Each failure in Section 13 has a passing automated test. |
| AC-08 | Latency targets in Section 14.1 are met at p95 in the reference scenario. |
| AC-09 | Chaos batch of ≥ 1,000 runs (target 10,000) reports zero safety violations. |
| AC-10 | UI survives backend disconnect and recovers automatically. |
| AC-11 | Each role sees only its permitted screens and data. |
| AC-12 | `docker compose up` starts the full system from a clean clone. |

### 19.3 Reference scenario
- Seed: fixed (e.g., 4417).
- Peak-hour demand on all intersections.
- Ambulance P1 from A6 to hospital at F1.
- Accident closes a road on the active route mid-trip.

---

## 20. Success Metrics

| Metric | Target |
|---|---|
| Ambulance travel time reduction vs baseline | Significant reduction; report measured value |
| Ambulance stops at preempted intersections | 0 |
| Safety invariant violations | 0 |
| Max cross-street hold | ≤ 90 s |
| Average added cross-street delay | Reported and minimised |
| Reroute latency (closure → commands) | < 500 ms p95 |
| Corridor tick latency | < 100 ms p95 |
| Chaos runs without violation | 100% |

---

## 21. Tech Stack and Repository Structure

### 21.1 Stack
| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, asyncio, Pydantic |
| Routing | Custom A* (heapq), custom Yen's k-shortest |
| Storage | In-memory state; SQLite event store |
| Testing | pytest, Hypothesis |
| Linting | ruff |
| Frontend | React, Vite, TypeScript, SVG map |
| Real-time | WebSocket |
| Auth | JWT |
| Packaging | Docker, Docker Compose |
| Production path (documented) | Redis (hot state), PostgreSQL + PostGIS (network config), Kafka / Redis Streams (event bus) |

### 21.2 Repository structure
```
corridor/
├── backend/
│   ├── core/          # domain models, SimClock, config, interfaces
│   ├── sim/           # city, traffic, simulated ambulance/sensors/network
│   ├── safety/        # safety supervisor
│   ├── controller/    # intersection controller state machine
│   ├── state/         # world state, freshness, historical profiles
│   ├── tracking/      # tracker, ETA, dead reckoning
│   ├── routing/       # cost model, A*, Yen's
│   ├── corridor/      # preemption timing, leases, neighbour coordination
│   ├── events/        # event bus, event store, replay
│   ├── health/        # heartbeats, mode manager
│   ├── chaos/         # injection, invariant checker, chaos runner
│   ├── api/           # REST, WebSocket, auth
│   └── tests/
├── frontend/
│   ├── src/operator/
│   ├── src/crew/
│   └── src/hospital/
├── docs/
│   ├── design.md
│   └── diagrams/
├── config/
│   ├── city.json
│   └── settings.yaml
├── docker-compose.yml
└── README.md
```

---

## 22. Delivery Phases

| Phase | Name | Key deliverable | Done when |
|---|---|---|---|
| 0 | Project setup | Skeleton, Docker, CI lint | `docker compose up` shows backend OK |
| 1 | Domain model, clock, city | Entities, SimClock, city.json, interfaces | City loads; graph tests pass |
| 2 | Simulation engine | Traffic, controllers, ambulance, sensors, SimNetwork | Reproducible 10-min peak run |
| 3 | Safety layer | Supervisor, local checks, property tests | AC-01 passes |
| 4 | World state & tracking | Ingestion, freshness, tracker | Live ETAs incl. GPS loss |
| 5 | Routing | Cost model, A*, Yen's, hysteresis | Replan < 300 ms; tests pass |
| 6 | Corridor control | Timing, leases, neighbours, recovery, decision log | AC-03 passes — **Tier 1 complete** |
| 7 | Dynamic events | Closure, deviation, multi-ambulance, hospital reroute | AC-04, AC-05 pass |
| 8 | Resilience & event store | Event bus/store, health, modes, injection API, replay engine | AC-07 passes |
| 9 | API, real-time, roles | REST, WebSocket, JWT roles, idempotency | AC-11 passes |
| 10 | Operator dashboard | Live Control, Scenarios, Health, Intersection | AC-10 passes |
| 11 | Crew app & hospital | Mobile crew app, arrivals screen | **Tier 2 complete** |
| 12 | Chaos testing | Invariant checker, chaos runner, report | AC-09 passes |
| 13 | Metrics & tracing | Baseline, latency tracing, Impact, Replay UI | AC-08 reported — **Tier 3 complete** |
| 14 | Docs, demo, prep | design.md, README, demo video, Q&A prep | AC-12 passes; package ready |

---

## 23. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Corridor timing bugs take longer than expected | Core feature delayed | Build Phase 6 early; extensive scenario tests; protect time |
| Simulation unrealistic | Weak credibility | Use standard models (BPR, discharge headway); document limits |
| Non-determinism breaks replay | Chaos/replay unusable | Enforce SimClock and seeded RNG from Phase 1; determinism test |
| Scope creep in UI | Less time for reasoning work | Tiered scope; UI priority order; cut Metrics/Replay UI first |
| Async race conditions | Flaky behaviour | Single writer per intersection; versioned commands; event ordering |
| Real GPS mode unreliable indoors | Demo failure | Simulated GPS default; real GPS optional |
| Over-engineering | Hard to explain | Keep modules small; document rejected alternatives |

---

## 24. Assumptions and Limitations

### Assumptions
- Vehicles queue in lanes and discharge at a stable headway.
- The ambulance follows the route unless deviation is detected.
- Intersection controllers can execute preemption when reachable.
- Historical time-of-day profiles represent typical traffic.

### Limitations
- Simplified queue model; does not capture Indian mixed traffic (two-wheelers, autos, lane indiscipline) accurately.
- A green signal does not guarantee drivers make way.
- Greedy lookahead corridor planning, not globally optimal network control.
- Central planner is a single point of failure for performance (not safety).
- Sensors, vehicles and hardware are simulated; no real NTCIP integration.
- Unusual events (festivals, protests) reduce historical profile accuracy.

---

## 25. Future Work

- SUMO integration via TraCI for vehicle-level validation.
- Model predictive control for corridor timing.
- Joint multi-ambulance optimisation across the network.
- Replicated planner with leader election (Raft) and zone partitioning for 5,000+ intersections.
- Learned ETA models (outside the safety path).
- V2X / public alerts to drivers ahead of the ambulance.
- Real hardware integration via NTCIP 1202.
- Integration with 108 emergency dispatch and existing city adaptive control systems.
- Hardware-in-the-loop demo with physical signal controllers.

---

## 26. Glossary

| Term | Meaning |
|---|---|
| Phase | A set of non-conflicting movements that are green together |
| Amber | Yellow change interval before red |
| All-red | Interval where all directions are red to clear the junction |
| Conflict matrix | Table of movements that may never be green together |
| Preemption | Overriding normal signal operation to give priority to an emergency vehicle |
| Lease | Time-limited preemption grant that expires unless renewed |
| Plan version | Monotonic number identifying the latest corridor plan |
| Idempotency key | Unique ID ensuring a command is executed at most once |
| Lookahead horizon | How far ahead (in travel time) the corridor is planned |
| Inflow gating | Holding side streets so they don't refill the corridor |
| Recovery | Returning a signal to normal operation after preemption |
| BPR function | Standard model relating travel time to traffic volume and capacity |
| A* | Shortest-path search algorithm guided by a heuristic |
| Yen's algorithm | Algorithm for k shortest loopless paths |
| Hysteresis | Threshold preventing frequent switching between options |
| Dead reckoning | Estimating position from last known speed and heading |
| EWMA | Exponentially weighted moving average |
| Digital twin | Real software operating on a virtual copy of the physical system |
| Invariant | A rule that must always be true |
| Chaos testing | Deliberately injecting random failures to test resilience |
| NTCIP | Standard protocols for communicating with traffic devices |