import type { BadgeKind, SourceKey } from "@/lib/derive";

const STATUS_STYLES: Record<BadgeKind, string> = {
  live: "bg-emerald-400 text-emerald-950",
  early: "bg-lime-300 text-lime-950",
  conflict: "bg-red-500 text-white",
  ghost: "bg-amber-400 text-amber-950",
  closed: "bg-neutral-500 text-white",
  pending: "bg-sky-400 text-sky-950",
};

export function StatusBadge({ kind, label }: { kind: BadgeKind; label: string }) {
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-bold tracking-wide ${STATUS_STYLES[kind]}`}>
      {label}
    </span>
  );
}

const SOURCE_STYLES: Record<SourceKey, string> = {
  "you-search": "bg-violet-500/15 text-violet-200 ring-violet-400/50",
  "you-contents": "bg-fuchsia-500/15 text-fuchsia-200 ring-fuchsia-400/50",
  "you-research": "bg-pink-500/15 text-pink-200 ring-pink-400/50",
  opencrawl: "bg-cyan-500/15 text-cyan-200 ring-cyan-400/50",
  direct: "bg-neutral-500/15 text-neutral-300 ring-neutral-400/40",
};

export function SourceBadge({ source, label }: { source: SourceKey; label: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${SOURCE_STYLES[source]}`}
    >
      {label}
    </span>
  );
}

export function Gauge({ label, value }: { label: string; value: number }) {
  const tone = value >= 75 ? "bg-emerald-400" : value >= 40 ? "bg-amber-400" : "bg-red-500";
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="min-w-0 flex-1">
      <div className="mb-1 flex items-baseline justify-between text-[11px] font-medium uppercase tracking-wide text-neutral-400">
        <span>{label}</span>
        <span className="font-mono text-sm font-bold text-white">{pct}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
        <div className={`h-full rounded-full transition-all duration-700 ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
