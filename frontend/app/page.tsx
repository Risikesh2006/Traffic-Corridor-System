"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type BackendStatus = "checking" | "ok" | "unreachable";

export default function Home() {
  const [status, setStatus] = useState<BackendStatus>("checking");

  useEffect(() => {
    let cancelled = false;

    fetch(`${API_URL}/api/v1/health`)
      .then((res) => {
        if (!res.ok) throw new Error(`status ${res.status}`);
        return res.json();
      })
      .then(() => {
        if (!cancelled) setStatus("ok");
      })
      .catch(() => {
        if (!cancelled) setStatus("unreachable");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 px-4">
      <h1 className="text-2xl font-semibold tracking-tight">Corridor Control</h1>
      <p className="max-w-md text-center text-neutral-400">
        Emergency Traffic Corridor — digital twin control system.
      </p>
      <StatusBadge status={status} />
    </main>
  );
}

function StatusBadge({ status }: { status: BackendStatus }) {
  const config: Record<BackendStatus, { label: string; className: string }> = {
    checking: { label: "Backend: checking…", className: "bg-neutral-800 text-neutral-300" },
    ok: { label: "Backend: OK", className: "bg-emerald-950 text-emerald-400" },
    unreachable: { label: "Backend: unreachable", className: "bg-red-950 text-red-400" },
  };
  const { label, className } = config[status];

  return (
    <span className={`rounded-full px-4 py-1.5 text-sm font-medium ${className}`}>{label}</span>
  );
}
