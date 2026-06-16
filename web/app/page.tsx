"use client";

import { useEffect, useState, useCallback } from "react";
import {
  motion,
  useReducedMotion,
  type Transition,
} from "framer-motion";
import {
  SealCheck,
  HandPalm,
  ShieldCheck,
  Lock,
  Pulse,
  TrendUp,
  TrendDown,
} from "@phosphor-icons/react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const POLL_MS = 5000;

// ── Types ────────────────────────────────────────────────────────────────────

interface Position {
  symbol?: string;
  side?: string;
  size?: number;
  qty?: number;
  entry_price?: number;
  stop_loss?: number;
  take_profit?: number;
  pnl?: number;
}

interface StatusData {
  mode?: string;
  venue?: string;
  halted?: boolean;
  equity?: number | null;
  available?: number | null;
  currency?: string;
  realized_pnl?: number | null;
  open_pnl?: number | null;
  positions?: Position[];
  // tolerated extras
  max_daily_loss?: number | null;
  daily_loss?: number | null;
  agent_id?: string;
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
  baseline_qty?: number | null;
  llm_size_factor?: number | null;
  llm_action_hint?: string;
  entry?: number;
  stop_loss?: number;
  take_profit?: number;
  leverage?: number;
  outcome?: string;
  reasoning?: string;
}

// ── Formatting ───────────────────────────────────────────────────────────────

function fmtNum(
  v: number | null | undefined,
  decimals = 2,
): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtSigned(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = fmtNum(Math.abs(v), decimals);
  if (v > 0) return `+${s}`;
  if (v < 0) return `−${s}`;
  return s;
}

function fmtTime(ts: string | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0)
    return "text-text";
  return v > 0 ? "text-long" : "text-short";
}

// Stable short agent-id placeholder (ERC-8004 identity nod).
function shortAgentId(raw?: string): string {
  if (raw && raw.length > 10) return `${raw.slice(0, 6)}…${raw.slice(-4)}`;
  if (raw) return raw;
  return "0xMIDAS…8004";
}

// ── Motion primitives ────────────────────────────────────────────────────────

const EASE: Transition["ease"] = [0.22, 0.61, 0.36, 1];

function usePanelMotion(index: number) {
  const reduce = useReducedMotion();
  if (reduce) {
    return { initial: false as const };
  }
  return {
    initial: { opacity: 0, y: 10 },
    animate: { opacity: 1, y: 0 },
    transition: {
      duration: 0.45,
      ease: EASE,
      delay: 0.05 + index * 0.06,
    } satisfies Transition,
  };
}

// ── Shell ────────────────────────────────────────────────────────────────────

function Panel({
  title,
  icon,
  index,
  span = "",
  children,
}: {
  title?: string;
  icon?: React.ReactNode;
  index: number;
  span?: string;
  children: React.ReactNode;
}) {
  const m = usePanelMotion(index);
  return (
    <motion.section
      {...m}
      className={`border border-line bg-surface ${span}`}
    >
      {title && (
        <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          {icon && <span className="text-muted">{icon}</span>}
          <h2 className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
            {title}
          </h2>
        </header>
      )}
      <div className="p-4">{children}</div>
    </motion.section>
  );
}

// ── Status strip ─────────────────────────────────────────────────────────────

function StatusStrip({
  status,
  offline,
}: {
  status: StatusData | null;
  offline: boolean;
}) {
  const reduce = useReducedMotion();
  const halted = status?.halted ?? false;
  const mode = status?.mode ?? "—";
  const venue = status?.venue ?? "—";

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-ink/95 backdrop-blur supports-[backdrop-filter]:bg-ink/80">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 md:px-6">
        {/* Brand mark */}
        <div className="flex items-baseline gap-2">
          <span className="font-display text-base font-bold tracking-tight text-gold">
            MIDAS
          </span>
          <span className="font-display text-[11px] uppercase tracking-[0.2em] text-muted">
            mission control
          </span>
        </div>

        {/* Mode + venue */}
        <div className="flex items-center gap-2">
          <span className="border border-line px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-text">
            {mode}
          </span>
          <span className="font-mono text-[11px] uppercase tracking-wide text-muted">
            {venue}
          </span>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2">
          {/* ERC-8004 identity badge */}
          <div
            className="flex items-center gap-1.5"
            title="ERC-8004 on-chain agent identity"
          >
            <SealCheck size={15} weight="fill" className="text-ai" />
            <span className="font-mono text-[11px] text-muted">
              {shortAgentId(status?.agent_id)}
            </span>
          </div>

          {/* Offline dot */}
          {offline && (
            <div className="flex items-center gap-1.5" title="API offline">
              <span className="h-1.5 w-1.5 rounded-full bg-muted" />
              <span className="font-mono text-[11px] text-muted">offline</span>
            </div>
          )}

          {/* Kill-switch pill */}
          <motion.div
            animate={
              reduce ? undefined : { opacity: 1 }
            }
            transition={{ duration: 0.4, ease: EASE }}
            className={`flex items-center gap-1.5 border px-2.5 py-1 transition-colors duration-500 ${
              halted
                ? "border-short/50 bg-short/10 text-short"
                : "border-long/50 bg-long/10 text-long"
            }`}
          >
            <HandPalm size={14} weight="bold" />
            <span className="font-display text-[11px] font-bold uppercase tracking-[0.16em]">
              {halted ? "Halted" : "Armed"}
            </span>
          </motion.div>
        </div>
      </div>
    </header>
  );
}

// ── Chips ────────────────────────────────────────────────────────────────────

function Chip({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "risk" | "ai";
}) {
  const toneClass =
    tone === "risk"
      ? "border-short/40 text-short"
      : tone === "ai"
        ? "border-ai/40 text-ai"
        : "border-line text-muted";
  return (
    <span
      className={`inline-block border px-1.5 py-0.5 font-mono text-[10px] uppercase leading-none tracking-wide ${toneClass}`}
    >
      {children}
    </span>
  );
}

// ── Authority Track (the signature) ──────────────────────────────────────────

type Stamp = {
  label: string;
  cls: string;
};

function terminalStamp(d: Decision): Stamp {
  const outcome = (d.outcome ?? "").toLowerCase();
  switch (outcome) {
    case "opened":
      return { label: "Opened", cls: "text-long border-long/50 bg-long/10" };
    case "skipped_veto":
      return { label: "Veto", cls: "text-muted border-line" };
    case "skipped_policy":
      return {
        label: "Policy halt",
        cls: "text-short border-short/50 bg-short/10",
      };
    case "skipped_zero_size":
      return { label: "Zero size", cls: "text-muted border-line" };
    case "closed":
      return { label: "Closed", cls: "text-text border-line" };
    default:
      if (outcome) return { label: d.outcome as string, cls: "text-muted border-line" };
      // fall back to allow flag when outcome absent
      if (d.allow === false)
        return { label: "Blocked", cls: "text-short border-short/50 bg-short/10" };
      return { label: "Pending", cls: "text-muted border-line" };
  }
}

function AuthorityRow({ d }: { d: Decision }) {
  const reduce = useReducedMotion();

  // The bar animates only on mount. Because each row is keyed by a stable
  // decision identity, React keeps the same instance across polls and does not
  // remount it — so unchanged rows never re-animate on refresh. Only a freshly
  // mounted (new) decision plays the clamp animation.

  const baseline =
    d.baseline_qty != null && d.baseline_qty > 0 ? d.baseline_qty : null;
  const qty = d.qty ?? 0;

  // Track domain: baseline is the inviolable ceiling. Without an advisor,
  // the track is simply the deterministic qty filled to full.
  const hasAdvisor = baseline != null;
  const ceiling = hasAdvisor ? baseline : qty > 0 ? qty : 1;
  const filledRatio = ceiling > 0 ? Math.min(qty / ceiling, 1) : 0;
  const clamped = hasAdvisor && qty < baseline - 1e-9;

  const stamp = terminalStamp(d);

  // The single expressive motion: bar grows from baseline → final on mount.
  const shouldAnim = !reduce;
  const barTransition: Transition = {
    duration: 0.7,
    ease: EASE,
    delay: 0.05,
  };

  return (
    <div className="grid grid-cols-1 gap-3 border-b border-line px-4 py-3.5 transition-colors hover:bg-surface2/40 md:grid-cols-[200px_1fr_220px] md:items-center md:gap-4">
      {/* LEFT — identity */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <time className="font-mono text-[11px] tabular-nums text-muted">
            {fmtTime(d.ts)}
          </time>
          <span className="font-display text-[13px] font-semibold tracking-tight text-text">
            {d.setup_ref ?? "—"}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {d.regime && <Chip>{d.regime}</Chip>}
          {d.risk_flag && d.risk_flag.toLowerCase() !== "none" && (
            <Chip tone="risk">{d.risk_flag}</Chip>
          )}
        </div>
      </div>

      {/* CENTER — the authority bar */}
      <div className="min-w-0">
        <div className="relative h-7 w-full border border-line bg-ink">
          {/* clamped-away remainder (faint AI) sits under the ceiling */}
          {clamped && (
            <div
              className="absolute inset-y-0 right-0 border-l border-dashed border-ai/40 bg-ai/[0.07]"
              style={{ width: `${(1 - filledRatio) * 100}%` }}
              aria-hidden
            />
          )}

          {/* filled = final qty */}
          <motion.div
            className="absolute inset-y-0 left-0 bg-gold/25"
            initial={
              shouldAnim ? { width: "100%" } : false
            }
            animate={{ width: `${filledRatio * 100}%` }}
            transition={shouldAnim ? barTransition : { duration: 0 }}
          >
            <span className="absolute inset-y-0 right-0 w-px bg-gold" />
          </motion.div>

          {/* gold ceiling tick + lock at baseline (inviolable max) */}
          {hasAdvisor && (
            <div
              className="absolute inset-y-0 right-0 flex items-center"
              title="Deterministic ceiling — the agent can shrink size but never exceed it"
            >
              <span className="h-full w-[2px] bg-gold" />
              <Lock
                size={11}
                weight="fill"
                className="absolute -right-0.5 top-1/2 -translate-y-1/2 translate-x-full text-gold"
              />
            </div>
          )}

          {/* readout overlay */}
          <div className="pointer-events-none absolute inset-0 flex items-center justify-between px-2.5">
            <span className="font-mono text-[11px] font-medium tabular-nums text-text">
              {fmtNum(qty, 4)}
            </span>
            {clamped && (
              <span className="font-mono text-[10px] tabular-nums text-ai">
                ×{fmtNum(d.llm_size_factor, 2)}
              </span>
            )}
          </div>
        </div>

        {/* caption */}
        <div className="mt-1 flex items-center justify-between font-mono text-[10px] text-muted">
          <span>
            {hasAdvisor
              ? clamped
                ? `Advisor trimmed to ${fmtNum(qty, 4)} of ${fmtNum(baseline, 4)}`
                : `At deterministic size · ${fmtNum(baseline, 4)}`
              : "Deterministic size · no advisor"}
          </span>
          {d.llm_action_hint && (
            <span className="text-ai">{d.llm_action_hint}</span>
          )}
        </div>
      </div>

      {/* RIGHT — terminal stamp + reasoning */}
      <div className="flex flex-col gap-1.5 md:items-end md:text-right">
        <span
          className={`inline-block border px-2 py-0.5 font-display text-[11px] font-semibold uppercase tracking-[0.12em] ${stamp.cls}`}
        >
          {stamp.label}
        </span>
        {d.reasoning && (
          <p
            className="line-clamp-2 max-w-[28ch] font-sans text-[11px] leading-snug text-muted"
            title={d.reasoning}
          >
            {d.reasoning}
          </p>
        )}
      </div>
    </div>
  );
}

function rowKey(d: Decision, i: number): string {
  // Stable identity per decision so React preserves row instances across polls
  // (no re-animation) while genuinely new decisions mount fresh and animate.
  return `${d.ts ?? "t"}|${d.setup_ref ?? "s"}|${d.outcome ?? "o"}|${i}`;
}

function DecisionFeed({ decisions }: { decisions: Decision[] }) {
  return (
    <Panel title="Decision feed" icon={<Pulse size={14} />} index={1}>
      {decisions.length === 0 ? (
        <p className="py-8 text-center font-sans text-sm text-muted">
          Waiting for the agent to start.
        </p>
      ) : (
        <div className="-mx-4 -mb-4 divide-y divide-line">
          {decisions.map((d, i) => (
            <AuthorityRow key={rowKey(d, i)} d={d} />
          ))}
        </div>
      )}
    </Panel>
  );
}

// ── Performance ──────────────────────────────────────────────────────────────

function Metric({
  label,
  value,
  className = "",
  big = false,
}: {
  label: string;
  value: React.ReactNode;
  className?: string;
  big?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-sans text-[11px] uppercase tracking-wide text-muted">
        {label}
      </span>
      <span
        className={`font-mono tabular-nums ${
          big ? "text-[26px] leading-none" : "text-[15px]"
        } ${className}`}
      >
        {value}
      </span>
    </div>
  );
}

function PerformancePanel({
  status,
  decisions,
}: {
  status: StatusData | null;
  decisions: Decision[];
}) {
  const currency = status?.currency ?? "";

  // Win-rate: opened (won) vs actionable (decisions that resolved to a trade
  // attempt — opened or closed). Best-effort from the log.
  const actionable = decisions.filter((d) => {
    const o = (d.outcome ?? "").toLowerCase();
    return o === "opened" || o === "closed";
  });
  const opened = decisions.filter(
    (d) => (d.outcome ?? "").toLowerCase() === "opened",
  );
  const winRate =
    actionable.length > 0 ? opened.length / actionable.length : null;

  const equity = status?.equity;

  return (
    <Panel title="Performance" icon={<TrendUp size={14} />} index={2}>
      <div className="flex flex-col gap-5">
        <Metric
          label={`Equity${currency ? ` · ${currency}` : ""}`}
          big
          value={equity != null ? fmtNum(equity, 2) : "—"}
        />
        <div className="grid grid-cols-2 gap-x-4 gap-y-4">
          <Metric
            label="Realized PnL"
            value={fmtSigned(status?.realized_pnl)}
            className={signClass(status?.realized_pnl)}
          />
          <Metric
            label="Open PnL"
            value={fmtSigned(status?.open_pnl)}
            className={signClass(status?.open_pnl)}
          />
          <Metric
            label="Available"
            value={status?.available != null ? fmtNum(status.available, 2) : "—"}
          />
          <Metric
            label="Win rate"
            value={
              winRate != null ? (
                <>
                  {(winRate * 100).toFixed(0)}
                  <span className="text-muted">%</span>
                  <span className="ml-1.5 text-[11px] text-muted">
                    {opened.length}/{actionable.length}
                  </span>
                </>
              ) : (
                "—"
              )
            }
          />
        </div>
      </div>
    </Panel>
  );
}

// ── Policy / Risk — the gate ─────────────────────────────────────────────────

function PolicyPanel({
  status,
  decisions,
}: {
  status: StatusData | null;
  decisions: Decision[];
}) {
  const halted = status?.halted ?? false;

  // Most recent risk flag from the feed (newest-first already).
  const latestRisk = decisions.find(
    (d) => d.risk_flag && d.risk_flag.toLowerCase() !== "none",
  )?.risk_flag;

  // Daily-loss meter toward the kill-switch threshold (if derivable).
  const cap =
    typeof status?.max_daily_loss === "number" && status.max_daily_loss
      ? Math.abs(status.max_daily_loss)
      : null;
  const lossRaw =
    typeof status?.daily_loss === "number" ? Math.abs(status.daily_loss) : null;
  const lossRatio =
    cap != null && lossRaw != null ? Math.min(lossRaw / cap, 1) : null;

  const allowed = !halted && !latestRisk;

  return (
    <Panel title="Policy & risk" icon={<ShieldCheck size={14} />} index={3}>
      <div className="flex flex-col gap-5">
        {/* Next-trade line */}
        <div
          className={`border-l-2 pl-3 ${
            allowed ? "border-long" : "border-short"
          }`}
        >
          <p className="font-sans text-[13px] leading-snug text-text">
            {halted
              ? "Trading is halted — the kill switch is engaged."
              : latestRisk
                ? `Next trade is blocked — risk flag “${latestRisk}”.`
                : "Next trade is allowed."}
          </p>
        </div>

        {/* Daily-loss meter */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="font-sans text-[11px] uppercase tracking-wide text-muted">
              Daily loss
            </span>
            <span className="font-mono text-[11px] tabular-nums text-muted">
              {lossRaw != null ? fmtNum(lossRaw, 2) : "—"}
              {cap != null && (
                <span className="text-muted"> / {fmtNum(cap, 2)}</span>
              )}
            </span>
          </div>
          <div className="h-2 w-full border border-line bg-ink">
            {lossRatio != null ? (
              <div
                className="h-full bg-short transition-[width] duration-500"
                style={{ width: `${lossRatio * 100}%` }}
              />
            ) : null}
          </div>
          {lossRatio == null && (
            <span className="font-sans text-[11px] text-muted">
              No loss limit reported.
            </span>
          )}
        </div>
      </div>
    </Panel>
  );
}

// ── Positions ────────────────────────────────────────────────────────────────

function sideTone(side?: string): string {
  const s = (side ?? "").toLowerCase();
  if (s.startsWith("l") || s === "buy") return "text-long";
  if (s.startsWith("s") || s === "sell") return "text-short";
  return "text-text";
}

function PositionsPanel({ status }: { status: StatusData | null }) {
  const positions = status?.positions ?? [];

  return (
    <Panel title="Open positions" index={4} span="md:col-span-2">
      {positions.length === 0 ? (
        <p className="py-4 font-sans text-sm text-muted">No open positions.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-line font-sans text-[10px] uppercase tracking-wide text-muted">
                <th className="pb-2 pr-4 font-medium">Symbol</th>
                <th className="pb-2 pr-4 font-medium">Side</th>
                <th className="pb-2 pr-4 text-right font-medium">Qty</th>
                <th className="pb-2 pr-4 text-right font-medium">Entry</th>
                <th className="pb-2 pr-4 text-right font-medium">Stop</th>
                <th className="pb-2 pr-4 text-right font-medium">Target</th>
                <th className="pb-2 text-right font-medium">PnL</th>
              </tr>
            </thead>
            <tbody className="font-mono text-[12px] tabular-nums">
              {positions.map((p, i) => {
                const side = p.side ?? "—";
                const Arrow = sideTone(p.side) === "text-short" ? TrendDown : TrendUp;
                return (
                  <tr key={i} className="border-b border-line/60">
                    <td className="py-2 pr-4 font-semibold text-text">
                      {p.symbol ?? "—"}
                    </td>
                    <td className={`py-2 pr-4 ${sideTone(side)}`}>
                      <span className="inline-flex items-center gap-1 uppercase">
                        <Arrow size={12} weight="bold" />
                        {side}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-right text-text">
                      {fmtNum(p.qty ?? p.size, 4)}
                    </td>
                    <td className="py-2 pr-4 text-right text-text">
                      {fmtNum(p.entry_price, 2)}
                    </td>
                    <td className="py-2 pr-4 text-right text-short">
                      {fmtNum(p.stop_loss, 2)}
                    </td>
                    <td className="py-2 pr-4 text-right text-long">
                      {fmtNum(p.take_profit, 2)}
                    </td>
                    <td className={`py-2 text-right ${signClass(p.pnl)}`}>
                      {fmtSigned(p.pnl, 2)}
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

// ── Page ─────────────────────────────────────────────────────────────────────

export default function MissionControlPage() {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [statusOk, setStatusOk] = useState(true);
  const [decisionsOk, setDecisionsOk] = useState(true);
  const [hasLoaded, setHasLoaded] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/status`, { cache: "no-store" });
      if (!res.ok) throw new Error("non-2xx");
      setStatus(await res.json());
      setStatusOk(true);
    } catch {
      setStatusOk(false);
    }

    try {
      const res = await fetch(`${API_BASE}/api/decisions?take=100`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error("non-2xx");
      const data: Decision[] = await res.json();
      // newest first
      setDecisions([...data].reverse());
      setDecisionsOk(true);
    } catch {
      setDecisionsOk(false);
    }

    setHasLoaded(true);
  }, []);

  useEffect(() => {
    // Subscribe to the external API: poll now, then every POLL_MS. State is
    // only set asynchronously inside fetchData (after the network resolves),
    // which is the intended "sync React with an external system" pattern.
    let active = true;
    const run = () => {
      if (active) void fetchData();
    };
    run();
    const id = setInterval(run, POLL_MS);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [fetchData]);

  const offline = hasLoaded && !statusOk && !decisionsOk;

  return (
    <div className="min-h-screen bg-ink text-text">
      <StatusStrip status={status} offline={offline} />

      <main className="mx-auto max-w-[1400px] px-4 py-6 md:px-6 md:py-8">
        {offline && (
          <p className="mb-6 border border-line bg-surface px-4 py-2.5 font-sans text-[13px] text-muted">
            API offline — showing last known.
          </p>
        )}

        {/* Centerpiece: decision feed (widest, top). */}
        <div className="mb-4">
          <DecisionFeed decisions={decisions} />
        </div>

        {/* Performance + Policy. */}
        <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <PerformancePanel status={status} decisions={decisions} />
          <PolicyPanel status={status} decisions={decisions} />
        </div>

        {/* Positions. */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <PositionsPanel status={status} />
        </div>
      </main>
    </div>
  );
}
