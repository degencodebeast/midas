"use client";

import { useEffect, useState, useCallback } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// ── Types ────────────────────────────────────────────────────────────────────

interface StatusData {
  mode?: string;
  equity?: number | null;
  halted?: boolean;
  kill_switch?: boolean;
  [key: string]: unknown;
}

interface Decision {
  ts?: string;
  setup_ref?: string;
  action?: string;
  allow?: boolean;
  size_multiplier?: number;
  gate_reason?: string;
  regime?: string;
  risk_flag?: string;
  context_status?: string;
  qty?: number;
  baseline_qty?: number;
  llm_size_factor?: number;
  llm_action_hint?: string;
  entry?: number;
  stop_loss?: number;
  take_profit?: number;
  leverage?: number;
  outcome?: string;
  reasoning?: string;
}

// ── Helper ───────────────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(decimals);
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)} %`;
}

// ── Sub-panels ───────────────────────────────────────────────────────────────

function StatusPanel({ status }: { status: StatusData | null }) {
  if (!status) {
    return (
      <Panel title="Status">
        <p className="text-zinc-400 text-sm">Connecting…</p>
      </Panel>
    );
  }
  const halted = status.halted ?? status.kill_switch ?? false;
  return (
    <Panel title="Status">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <dt className="text-zinc-500">Mode</dt>
        <dd className="font-medium">{status.mode ?? "—"}</dd>
        <dt className="text-zinc-500">Kill-switch / Halted</dt>
        <dd>
          <span
            className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ${
              halted
                ? "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300"
                : "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
            }`}
          >
            {halted ? "HALTED" : "RUNNING"}
          </span>
        </dd>
        <dt className="text-zinc-500">Equity</dt>
        <dd className="font-medium tabular-nums">
          {status.equity != null ? `$${Number(status.equity).toFixed(2)}` : "—"}
        </dd>
      </dl>
    </Panel>
  );
}

function PerformancePanel({
  status,
  decisions,
}: {
  status: StatusData | null;
  decisions: Decision[];
}) {
  // derive best-effort stats from decisions
  const decided = decisions.filter((d) => d.outcome);
  const wins = decided.filter(
    (d) => d.outcome === "win" || d.outcome === "profit"
  ).length;
  const winRate = decided.length > 0 ? wins / decided.length : null;

  const realizedPnl = (status as Record<string, unknown>)?.realized_pnl as
    | number
    | undefined;
  const openPnl = (status as Record<string, unknown>)?.open_pnl as
    | number
    | undefined;

  return (
    <Panel title="Performance">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <dt className="text-zinc-500">Equity</dt>
        <dd className="font-medium tabular-nums">
          {status?.equity != null ? `$${Number(status.equity).toFixed(2)}` : "—"}
        </dd>
        <dt className="text-zinc-500">Realized PnL</dt>
        <dd className="font-medium tabular-nums">{fmt(realizedPnl)}</dd>
        <dt className="text-zinc-500">Open PnL</dt>
        <dd className="font-medium tabular-nums">{fmt(openPnl)}</dd>
        <dt className="text-zinc-500">Win-rate (from log)</dt>
        <dd className="font-medium tabular-nums">
          {winRate != null ? fmtPct(winRate) : "—"}
          {decided.length > 0 && (
            <span className="ml-1 text-xs text-zinc-400">
              ({wins}/{decided.length})
            </span>
          )}
        </dd>
      </dl>
    </Panel>
  );
}

function PositionsPanel({ status }: { status: StatusData | null }) {
  const positions = (status as Record<string, unknown>)?.positions as
    | unknown[]
    | undefined;

  return (
    <Panel title="Positions">
      {!positions || positions.length === 0 ? (
        <p className="text-zinc-400 text-sm">No open positions reported.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-zinc-500 border-b border-zinc-200 dark:border-zinc-700">
                <th className="pb-1 pr-3">Symbol</th>
                <th className="pb-1 pr-3">Side</th>
                <th className="pb-1 pr-3">Qty</th>
                <th className="pb-1">PnL</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos, i) => {
                const p = pos as Record<string, unknown>;
                return (
                  <tr
                    key={i}
                    className="border-b border-zinc-100 dark:border-zinc-800"
                  >
                    <td className="py-1 pr-3 font-medium">
                      {String(p.symbol ?? "—")}
                    </td>
                    <td className="py-1 pr-3">{String(p.side ?? "—")}</td>
                    <td className="py-1 pr-3 tabular-nums">
                      {String(p.qty ?? "—")}
                    </td>
                    <td className="py-1 tabular-nums">
                      {String(p.pnl ?? "—")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function DecisionFeed({ decisions }: { decisions: Decision[] }) {
  return (
    <Panel title="Decision Feed — centrepiece (baseline_qty → qty clamp visible)">
      {decisions.length === 0 ? (
        <p className="text-zinc-400 text-sm">No decisions yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="text-left text-zinc-500 border-b border-zinc-200 dark:border-zinc-700">
                <th className="pb-1 pr-3 whitespace-nowrap">Timestamp</th>
                <th className="pb-1 pr-3 whitespace-nowrap">Setup Ref</th>
                <th className="pb-1 pr-3 whitespace-nowrap">Regime / Risk</th>
                <th className="pb-1 pr-3 whitespace-nowrap">LLM Factor</th>
                <th className="pb-1 pr-3 whitespace-nowrap">LLM Hint</th>
                <th className="pb-1 pr-3 whitespace-nowrap font-bold text-amber-600 dark:text-amber-400">
                  baseline_qty → qty (clamp)
                </th>
                <th className="pb-1 pr-3 whitespace-nowrap">Outcome</th>
                <th className="pb-1 whitespace-nowrap">Reasoning</th>
              </tr>
            </thead>
            <tbody>
              {decisions.map((d, i) => {
                const clampApplied =
                  d.baseline_qty != null &&
                  d.qty != null &&
                  Math.abs(d.qty - d.baseline_qty) > 1e-9;
                return (
                  <tr
                    key={i}
                    className="border-b border-zinc-100 dark:border-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-900"
                  >
                    <td className="py-1 pr-3 tabular-nums text-zinc-400 whitespace-nowrap">
                      {d.ts
                        ? new Date(d.ts).toLocaleString()
                        : "—"}
                    </td>
                    <td className="py-1 pr-3 font-medium whitespace-nowrap">
                      {d.setup_ref ?? "—"}
                    </td>
                    <td className="py-1 pr-3 whitespace-nowrap">
                      <span className="mr-1">{d.regime ?? "—"}</span>
                      {d.risk_flag && (
                        <span className="rounded bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300 px-1">
                          {d.risk_flag}
                        </span>
                      )}
                    </td>
                    <td className="py-1 pr-3 tabular-nums">
                      {fmt(d.llm_size_factor, 3)}
                    </td>
                    <td className="py-1 pr-3">{d.llm_action_hint ?? "—"}</td>
                    <td
                      className={`py-1 pr-3 tabular-nums font-semibold whitespace-nowrap ${
                        clampApplied
                          ? "text-amber-600 dark:text-amber-400"
                          : ""
                      }`}
                    >
                      {d.baseline_qty != null ? fmt(d.baseline_qty, 4) : "—"}
                      {" → "}
                      {d.qty != null ? fmt(d.qty, 4) : "—"}
                      {clampApplied && (
                        <span className="ml-1 text-xs rounded bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300 px-1">
                          clamped
                        </span>
                      )}
                    </td>
                    <td className="py-1 pr-3">
                      {d.outcome ? (
                        <span
                          className={`rounded px-1 ${
                            d.outcome === "win" || d.outcome === "profit"
                              ? "bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300"
                              : d.outcome === "loss"
                              ? "bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300"
                              : "bg-zinc-100 dark:bg-zinc-700 text-zinc-600 dark:text-zinc-300"
                          }`}
                        >
                          {d.outcome}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-1 text-zinc-500 max-w-xs truncate">
                      {d.reasoning ?? "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function PolicyRiskPanel({ decisions }: { decisions: Decision[] }) {
  // Summarise gate reasons and blocked decisions
  const blocked = decisions.filter((d) => d.allow === false);
  const gateReasons: Record<string, number> = {};
  for (const d of decisions) {
    if (d.gate_reason) {
      gateReasons[d.gate_reason] = (gateReasons[d.gate_reason] ?? 0) + 1;
    }
  }
  return (
    <Panel title="Policy / Risk">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <dt className="text-zinc-500">Total decisions logged</dt>
        <dd className="font-medium tabular-nums">{decisions.length}</dd>
        <dt className="text-zinc-500">Blocked by policy</dt>
        <dd className="font-medium tabular-nums text-red-600 dark:text-red-400">
          {blocked.length}
        </dd>
      </dl>
      {Object.keys(gateReasons).length > 0 && (
        <div className="mt-3">
          <p className="text-xs text-zinc-500 mb-1">Gate reason breakdown:</p>
          <ul className="space-y-0.5">
            {Object.entries(gateReasons).map(([reason, count]) => (
              <li key={reason} className="text-xs flex justify-between">
                <span className="text-zinc-600 dark:text-zinc-400">{reason}</span>
                <span className="tabular-nums font-medium">{count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

// ── Card wrapper ─────────────────────────────────────────────────────────────

function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-sm p-4">
      <h2 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300 uppercase tracking-wide mb-3">
        {title}
      </h2>
      {children}
    </section>
  );
}

// ── Root page (client component) ─────────────────────────────────────────────

export default function MissionControlPage() {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [statusError, setStatusError] = useState(false);
  const [decisionsError, setDecisionsError] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchData = useCallback(async () => {
    // fetch status
    try {
      const res = await fetch(`${API_BASE}/api/status`);
      if (!res.ok) throw new Error("non-2xx");
      const data: StatusData = await res.json();
      setStatus(data);
      setStatusError(false);
    } catch {
      setStatusError(true);
    }

    // fetch decisions
    try {
      const res = await fetch(`${API_BASE}/api/decisions?take=100`);
      if (!res.ok) throw new Error("non-2xx");
      const data: Decision[] = await res.json();
      // newest first
      setDecisions([...data].reverse());
      setDecisionsError(false);
    } catch {
      setDecisionsError(true);
    }

    setLastUpdated(new Date());
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 p-4 md:p-8">
      {/* Header */}
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-zinc-900 dark:text-zinc-50">
            Magic Agent — Mission Control
          </h1>
          <p className="text-xs text-zinc-400 mt-0.5">
            Read-only dashboard · polling {API_BASE} every 5 s
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-zinc-400">
          {(statusError || decisionsError) && (
            <span className="rounded bg-red-100 dark:bg-red-900 text-red-600 dark:text-red-300 px-2 py-0.5">
              API unreachable
            </span>
          )}
          {lastUpdated && (
            <span>Updated {lastUpdated.toLocaleTimeString()}</span>
          )}
        </div>
      </header>

      {/* Top row: Status | Performance | Policy */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <StatusPanel status={status} />
        <PerformancePanel status={status} decisions={decisions} />
        <PolicyRiskPanel decisions={decisions} />
      </div>

      {/* Positions row */}
      <div className="mb-4">
        <PositionsPanel status={status} />
      </div>

      {/* Decision feed — centrepiece, full width */}
      <DecisionFeed decisions={decisions} />
    </div>
  );
}
