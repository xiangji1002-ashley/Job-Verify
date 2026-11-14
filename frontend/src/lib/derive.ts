import type { CitedClaim, JobView, LiveWireEvent } from "./types";

// ---------------------------------------------------------------- status badges
export type BadgeKind = "live" | "early" | "conflict" | "ghost" | "closed" | "pending";

const FRESH_MS = 48 * 3600 * 1000;

export function isEarlyWindow(job: JobView): boolean {
  const pub = job.last_evidence?.timestamps.published_at;
  const ref = job.last_checked_at ? Date.parse(job.last_checked_at) : Date.now();
  const age = pub ? ref - Date.parse(pub) : Number.NaN;
  const fresh = Number.isFinite(age) && age >= 0 && age < FRESH_MS;
  const signal = job.last_finder?.mirror_urls.some((m) => m.snippet_signals.some((s) => /early applicant/i.test(s))) ?? false;
  return fresh || signal;
}

export function badgeFor(job: JobView): { kind: BadgeKind; label: string } {
  if (job.status === "CLOSED") {
    const conflict = job.last_evidence?.conflicts.some((c) => c.code === "CONFLICT_CLOSED_ON_SOURCE");
    return conflict ? { kind: "conflict", label: "CONFLICT: CLOSED ON ATS" } : { kind: "closed", label: "CLOSED" };
  }
  if (job.status === "GHOST_RISK") return { kind: "ghost", label: "GHOST RISK" };
  if (job.status === "OPEN") return isEarlyWindow(job) ? { kind: "early", label: "EARLY WINDOW" } : { kind: "live", label: "VERIFIED LIVE" };
  return { kind: "pending", label: "PENDING" };
}

// ---------------------------------------------------------------- source attribution
export type SourceKey = "you-search" | "you-contents" | "you-research" | "opencrawl" | "direct";

const YOU_SEARCH_SIGNALS = new Set(["canonical", "mirror", "news", "conflict"]);

export function sourceFor(c: Pick<CitedClaim, "engine" | "signal">): { key: SourceKey; label: string } {
  if (c.engine === "OpenCrawl") return { key: "opencrawl", label: "OpenCrawl Live DOM" };
  if (c.engine === "You.com") {
    if (c.signal === "hiring_research") return { key: "you-research", label: "You.com Research" };
    if (c.signal && YOU_SEARCH_SIGNALS.has(c.signal)) return { key: "you-search", label: "You.com Search" };
    return { key: "you-contents", label: "You.com Contents" };
  }
  return { key: "direct", label: "Public ATS API (direct)" };
}

// ---------------------------------------------------------------- formatting
export function fmtDateTime(iso?: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtClock(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { hour12: false });
}

export function relTime(iso?: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (Number.isNaN(secs)) return "-";
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 172800) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

// ---------------------------------------------------------------- events
export function latestEvent(job: JobView, all: LiveWireEvent[], kind: string): LiveWireEvent | undefined {
  const pool = [...all.filter((e) => e.job_id === job.id), ...job.recent_events];
  return pool.filter((e) => e.kind === kind).sort((a, b) => (b.id ?? 0) - (a.id ?? 0))[0];
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
