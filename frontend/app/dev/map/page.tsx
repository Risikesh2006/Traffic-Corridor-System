"use client";

import { useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const POLL_MS = 500;

// Matches the backend's default city generator (grid, 300m spacing,
// columns A-F west->east, rows 1-6 north->south). This page is a
// development debug view, not the final dashboard.
const SPACING_M = 300;
const SCALE = 0.3; // px per metre
const MARGIN = 40;

function nodeXY(nodeId: string): [number, number] {
  const col = nodeId.charCodeAt(0) - "A".charCodeAt(0);
  const row = parseInt(nodeId.slice(1), 10) - 1;
  return [MARGIN + col * SPACING_M * SCALE, MARGIN + row * SPACING_M * SCALE];
}

function metresToPx(x: number, y: number): [number, number] {
  return [MARGIN + x * SCALE, MARGIN + y * SCALE];
}

interface IntersectionSnapshot {
  state: string;
  current_phase_id: string | null;
  signal_view: Record<string, string>;
}

interface SegmentSnapshot {
  queue_length: number;
  in_transit: number;
}

interface AmbulanceSnapshot {
  x: number;
  y: number;
  speed_mps: number;
  heading_deg: number;
  state: string;
  route_index: number;
  total_stops: number;
  current_segment_id: string | null;
}

interface EtaSnapshot {
  intersection_id: string;
  eta_expected_ms: number;
  eta_low_ms: number;
  eta_high_ms: number;
}

interface TrackedSnapshot {
  x: number;
  y: number;
  s_m: number;
  uncertainty_m: number;
  position_source: string;
  last_fix_age_ms: number | null;
  etas: EtaSnapshot[];
}

interface Snapshot {
  sim_time_ms: number;
  sim_time_label: string;
  seed: number;
  intersections: Record<string, IntersectionSnapshot>;
  segments: Record<string, SegmentSnapshot>;
  ambulances: Record<string, AmbulanceSnapshot>;
  tracked?: Record<string, TrackedSnapshot | string[]> & { stale_segments?: string[] };
  freshness?: Record<string, number>;
}

function queueColor(queueLength: number): string {
  if (queueLength <= 5) return "#22c55e";
  if (queueLength <= 15) return "#eab308";
  if (queueLength <= 25) return "#f97316";
  return "#ef4444";
}

function signalColor(state: string): string {
  switch (state) {
    case "GREEN":
      return "#22c55e";
    case "AMBER":
      return "#eab308";
    case "FLASHING_RED":
      return "#dc2626";
    default:
      return "#52525b";
  }
}

function secondsFrom(ms: number, nowMs: number): string {
  return `${Math.max(0, (ms - nowMs) / 1000).toFixed(0)}s`;
}

export default function DevMapPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const res = await fetch(`${API_URL}/api/v1/sim/snapshot`);
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data: Snapshot = await res.json();
        if (!cancelled) {
          setSnapshot(data);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("sim unavailable (start backend with DEV_SIM=1)");
      }
    };

    poll();
    timerRef.current = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const size = MARGIN * 2 + 5 * SPACING_M * SCALE;
  const tracked = snapshot?.tracked?.["AMB-1"] as TrackedSnapshot | undefined;
  const staleSet = new Set(snapshot?.tracked?.stale_segments ?? []);

  return (
    <main className="flex min-h-screen flex-col items-center gap-4 bg-neutral-950 p-6 text-neutral-100">
      <div className="flex w-full max-w-3xl items-center justify-between">
        <h1 className="text-lg font-semibold">
          Corridor Control — <span className="text-neutral-400">Developer view</span>
        </h1>
        <span className="font-mono text-sm text-neutral-400">
          {snapshot ? snapshot.sim_time_label : "--:--:--.---"}
        </span>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="rounded-lg border border-neutral-800 bg-neutral-900"
      >
        {snapshot &&
          Object.entries(snapshot.segments).map(([segmentId, seg]) => {
            const [fromId, toId] = segmentId.split("->");
            const [x1, y1] = nodeXY(fromId);
            const [x2, y2] = nodeXY(toId);
            return (
              <line
                key={segmentId}
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                stroke={queueColor(seg.queue_length)}
                strokeWidth={4}
                strokeLinecap="round"
                strokeDasharray={staleSet.has(segmentId) ? "6 6" : undefined}
                opacity={staleSet.has(segmentId) ? 0.6 : 1}
              />
            );
          })}

        {snapshot &&
          Object.entries(snapshot.intersections).map(([nodeId, node]) => {
            const [x, y] = nodeXY(nodeId);
            return (
              <g key={nodeId}>
                <circle cx={x} cy={y} r={8} fill={signalColor(node.state)} stroke="#0a0a0a" />
                <text x={x + 10} y={y - 8} fontSize={9} fill="#a1a1aa">
                  {nodeId}
                </text>
              </g>
            );
          })}

        {snapshot &&
          Object.entries(snapshot.ambulances).map(([vehicleId, amb]) => {
            const [px, py] = metresToPx(amb.x, amb.y);
            return (
              <g key={`truth-${vehicleId}`}>
                <circle
                  cx={px}
                  cy={py}
                  r={9}
                  fill="none"
                  stroke="#e5e7eb"
                  strokeWidth={1}
                  strokeDasharray="2 2"
                />
                <text x={px + 12} y={py + 12} fontSize={8} fill="#a1a1aa">
                  truth — dev only
                </text>
              </g>
            );
          })}

        {tracked && (
          <g>
            <circle
              cx={metresToPx(tracked.x, tracked.y)[0]}
              cy={metresToPx(tracked.x, tracked.y)[1]}
              r={Math.max(4, tracked.uncertainty_m * SCALE)}
              fill="#38bdf8"
              opacity={0.15}
              stroke="#38bdf8"
              strokeWidth={1}
            />
            <circle
              cx={metresToPx(tracked.x, tracked.y)[0]}
              cy={metresToPx(tracked.x, tracked.y)[1]}
              r={6}
              fill={tracked.position_source === "GPS" ? "#38bdf8" : "#f59e0b"}
              stroke="#0a0a0a"
              strokeWidth={1.5}
            />
          </g>
        )}
      </svg>

      <div className="w-full max-w-3xl text-sm">
        <p className="text-neutral-300">
          Tracked: {tracked ? tracked.position_source : "—"}
          {tracked && ` · uncertainty ±${tracked.uncertainty_m.toFixed(0)} m`}
          {tracked && tracked.last_fix_age_ms !== null && ` · last fix ${(tracked.last_fix_age_ms / 1000).toFixed(1)}s ago`}
        </p>
        {snapshot?.freshness && (
          <p className="text-neutral-400">
            Road data: fresh {snapshot.freshness.fresh ?? 0} · ageing {snapshot.freshness.ageing ?? 0} ·
            stale {snapshot.freshness.stale ?? 0}
          </p>
        )}
        {tracked && tracked.etas.length > 0 && (
          <table className="mt-2 w-full border-collapse text-left text-neutral-300">
            <thead>
              <tr className="text-neutral-500">
                <th className="py-1">Intersection</th>
                <th>Expected</th>
                <th>Window</th>
              </tr>
            </thead>
            <tbody>
              {tracked.etas.map((eta) => (
                <tr key={eta.intersection_id} className="border-t border-neutral-800">
                  <td className="py-1 font-mono">{eta.intersection_id}</td>
                  <td>{secondsFrom(eta.eta_expected_ms, snapshot?.sim_time_ms ?? 0)}</td>
                  <td>
                    {secondsFrom(eta.eta_low_ms, snapshot?.sim_time_ms ?? 0)} –{" "}
                    {secondsFrom(eta.eta_high_ms, snapshot?.sim_time_ms ?? 0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p className="text-xs text-neutral-500">
        Roads coloured by queue length; dashed roads have stale data. Solid dot: tracked estimate
        (blue GPS, amber dead reckoning) with its uncertainty circle. Dotted ring: true position.
        Polling every {POLL_MS}ms.
      </p>
    </main>
  );
}
