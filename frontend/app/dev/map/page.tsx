"use client";

import { useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const POLL_MS = 500;

// Matches the backend's default city generator (grid, 300m spacing,
// columns A-F west->east, rows 1-6 north->south). This page is a
// development debug view, not the final dashboard — it decodes node IDs
// directly rather than fetching a general-purpose city/map endpoint.
const SPACING_M = 300;
const SCALE = 0.3; // px per metre
const MARGIN = 40;

function nodeXY(nodeId: string): [number, number] {
  const col = nodeId.charCodeAt(0) - "A".charCodeAt(0);
  const row = parseInt(nodeId.slice(1), 10) - 1;
  return [MARGIN + col * SPACING_M * SCALE, MARGIN + row * SPACING_M * SCALE];
}

type SignalView = Record<string, string>;

interface IntersectionSnapshot {
  state: string;
  current_phase_id: string | null;
  signal_view: SignalView;
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

interface Snapshot {
  sim_time_ms: number;
  sim_time_label: string;
  seed: number;
  intersections: Record<string, IntersectionSnapshot>;
  segments: Record<string, SegmentSnapshot>;
  ambulances: Record<string, AmbulanceSnapshot>;
}

function queueColor(queueLength: number): string {
  if (queueLength <= 5) return "#22c55e"; // green
  if (queueLength <= 15) return "#eab308"; // amber
  if (queueLength <= 25) return "#f97316"; // orange
  return "#ef4444"; // red
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
      return "#52525b"; // ALL_RED / unknown
  }
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
            const px = MARGIN + amb.x * SCALE;
            const py = MARGIN + amb.y * SCALE;
            return (
              <g key={vehicleId}>
                <circle cx={px} cy={py} r={6} fill="#38bdf8" stroke="#0a0a0a" strokeWidth={1.5} />
                <text x={px + 8} y={py + 4} fontSize={9} fill="#38bdf8">
                  {vehicleId} ({amb.state}, {amb.total_stops} stops)
                </text>
              </g>
            );
          })}
      </svg>

      <p className="text-xs text-neutral-500">
        Roads coloured by queue length (green/amber/orange/red). Nodes coloured by signal state.
        Polling every {POLL_MS}ms.
      </p>
    </main>
  );
}
