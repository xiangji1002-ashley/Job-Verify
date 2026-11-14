"use client";

import { useState, type ReactNode } from "react";
import { AlertTriangle, Archive, Calculator, Check, Clock, Copy, ExternalLink, FileText, Globe, HelpCircle, MousePointerClick, Search, Star } from "lucide-react";
import { Gauge, SourceBadge, StatusBadge } from "@/components/Badges";
import { badgeFor, fmtDateTime, hostOf, latestEvent, sourceFor } from "@/lib/derive";
import type { CitedClaim, JobView, LiveWireEvent } from "@/lib/types";

// ---------------------------------------------------------------- small building blocks
function Receipt({ c }: { c: CitedClaim }) {
  const s = sourceFor(c);
  return (
    <li className="flex items-start gap-2 py-1.5">
      <SourceBadge source={s.key} label={s.label} />
      <span className="min-w-0 flex-1 text-[13px] leading-snug text-neutral-100">{c.claim}</span>
      <a href={c.source_url} target="_blank" rel="noreferrer" title={c.source_url} className="shrink-0 text-neutral-500 hover:text-white">
        <ExternalLink className="h-3.5 w-3.5" />
      </a>
    </li>
  );
}

function Receipts({ items, empty }: { items: CitedClaim[]; empty: string }) {
  if (items.length === 0) return <div className="py-2 text-sm text-neutral-500">{empty}</div>;
  return <ul className="divide-y divide-neutral-900">{items.map((c, i) => <Receipt key={`${c.signal}-${i}`} c={c} />)}</ul>;
}

function AgentSection({ n, title, powered, icon, children }: { n: number; title: string; powered: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-neutral-700 bg-neutral-950">
      <div className="flex items-center gap-3 border-b border-neutral-800 px-4 py-2.5">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white text-xs font-extrabold text-black">{n}</span>
        <span className="text-neutral-300">{icon}</span>
        <h3 className="text-sm font-bold text-white">{title}</h3>
        <span className="ml-auto text-[11px] text-neutral-400">{powered}</span>
      </div>
      <div className="space-y-3 px-4 py-3">{children}</div>
    </section>
  );
}

function Card({ title, icon, bad, children }: { title: string; icon: ReactNode; bad?: boolean; children: ReactNode }) {
  return (
    <div className={`rounded-md border p-3 ${bad ? "border-red-500/70 bg-red-950/30" : "border-neutral-700 bg-neutral-900/60"}`}>
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-neutral-400">
        {icon} {title}
      </div>
      {children}
    </div>
  );
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5 text-[13px]">
      <span className="text-neutral-400">{k}</span>
      <span className="min-w-0 break-all text-right font-mono text-neutral-100">{v}</span>
    </div>
  );
}

function ActionLines({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <ul className="space-y-0.5 font-mono text-[11px] text-neutral-400">
      {lines.map((l, i) => (
        <li key={i}>&gt; {l}</li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- evidence cards
function UrlDiffCard({ job }: { job: JobView }) {
  const f = job.last_finder;
  const e = job.last_evidence;
  const official = f?.canonical_ats_url ?? job.url;
  const mirror = f?.mirror_urls[0];
  const conflict = e?.conflicts.find((c) => c.code === "CONFLICT_CLOSED_ON_SOURCE");
  const officialClosed = job.status === "CLOSED";
  return (
    <Card title="Canonical ATS vs mirror" icon={<AlertTriangle className="h-3.5 w-3.5" />} bad={!!conflict}>
      <div className="space-y-2">
        <div className={`rounded border-l-4 px-2 py-1.5 ${officialClosed ? "border-red-500 bg-red-950/40" : "border-emerald-400 bg-emerald-950/30"}`}>
          <div className="flex items-center justify-between text-[11px] font-bold">
            <span className="text-neutral-200">OFFICIAL ATS ({f?.ats_provider ?? "ats"})</span>
            <span className={officialClosed ? "text-red-300" : "text-emerald-300"}>
              {officialClosed ? "CLOSED" : "LIVE"}
              {e ? ` · HTTP ${e.http_status || "?"}` : ""}
            </span>
          </div>
          <div className="break-all font-mono text-[11px] text-neutral-300">{official}</div>
        </div>
        {mirror ? (
          <div className={`rounded border-l-4 px-2 py-1.5 ${mirror.closed_in_snippet ? "border-neutral-500 bg-neutral-900" : "border-amber-400 bg-amber-950/30"}`}>
            <div className="flex items-center justify-between text-[11px] font-bold">
              <span className="text-neutral-200">MIRROR ({mirror.source})</span>
              <span className={mirror.closed_in_snippet ? "text-neutral-400" : "text-amber-300"}>
                {mirror.closed_in_snippet ? "SHOWS CLOSED" : "STILL LISTED"}
              </span>
            </div>
            <div className="break-all font-mono text-[11px] text-neutral-300">{mirror.url}</div>
            {mirror.snippet_signals.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {mirror.snippet_signals.map((s) => (
                  <span key={s} className="rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-200">&ldquo;{s}&rdquo;</span>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="text-xs text-neutral-500">No third-party mirror found.</div>
        )}
        {conflict && (
          <div className="rounded bg-red-500 px-2 py-1 text-[11px] font-bold text-white">
            CONFLICT_CLOSED_ON_SOURCE: {conflict.note ?? "official ATS closed while the mirror still lists the job"}
          </div>
        )}
      </div>
    </Card>
  );
}

function ApplyCard({ job }: { job: JobView }) {
  const e = job.last_evidence;
  if (!e) return null;
  const a = e.apply_button;
  const state = a.found && a.clickable ? "ENABLED" : a.found ? "DISABLED" : "NOT FOUND";
  const tone = a.found && a.clickable ? "text-emerald-300" : "text-red-300";
  return (
    <Card title="Apply button (live DOM)" icon={<MousePointerClick className="h-3.5 w-3.5" />} bad={!(a.found && a.clickable) && e.dom_reliable}>
      <div className={`text-lg font-extrabold ${tone}`}>{state}</div>
      <div className="mt-1 break-all font-mono text-[11px] text-neutral-400">{a.selector_or_receipt ?? "no selector / receipt"}</div>
      {!e.dom_reliable && <div className="mt-1 text-[11px] text-amber-300">DOM not a rendered capture; treated as uncertain.</div>}
    </Card>
  );
}

function TimestampCard({ job }: { job: JobView }) {
  const e = job.last_evidence;
  if (!e) return null;
  const t = e.timestamps;
  return (
    <Card title="Timestamps" icon={<Clock className="h-3.5 w-3.5" />}>
      <KV k="publishedAt" v={fmtDateTime(t.published_at)} />
      <KV k="datePosted (JSON-LD)" v={fmtDateTime(e.job_posting?.date_posted)} />
      <KV k="updatedAt" v={fmtDateTime(t.updated_at)} />
      <KV k="validThrough" v={fmtDateTime(t.valid_through)} />
      <KV k="source" v={t.source_type ?? "-"} />
    </Card>
  );
}

function UncertainCard({ job }: { job: JobView }) {
  const fields = job.last_verdict?.uncertain_fields ?? [];
  return (
    <Card title="Uncertain fields" icon={<HelpCircle className="h-3.5 w-3.5" />}>
      {fields.length === 0 ? (
        <div className="text-sm text-emerald-300">None - every scored field was directly observed.</div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {fields.map((f) => (
            <span key={f} className="rounded bg-amber-400/15 px-2 py-0.5 font-mono text-[11px] text-amber-200 ring-1 ring-inset ring-amber-400/40">{f}</span>
          ))}
        </div>
      )}
    </Card>
  );
}

function ActionCard({ job, events }: { job: JobView; events: LiveWireEvent[] }) {
  const [copied, setCopied] = useState(false);
  if (job.queue === "PRIORITY") {
    const ev = latestEvent(job, events, "outreach_draft");
    const draft = typeof ev?.data?.draft === "string" ? ev.data.draft : null;
    return (
      <div className="rounded-lg border-2 border-lime-300 bg-lime-950/20 p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-bold text-lime-300">
          <Star className="h-4 w-4" /> Autonomous action: promoted to Priority Apply Queue
          {ev?.simulated && <span className="rounded bg-amber-400 px-1.5 text-[10px] font-bold text-amber-950">SIMULATED</span>}
        </div>
        {draft ? (
          <>
            <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-neutral-400">
              <FileText className="h-3.5 w-3.5" /> Outreach draft (3 sentences, cites publishedAt)
              <button
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(draft);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  } catch {
                    /* clipboard unavailable */
                  }
                }}
                className="ml-auto flex items-center gap-1 rounded border border-neutral-600 px-1.5 py-0.5 text-[10px] normal-case text-neutral-200 hover:bg-neutral-800"
              >
                {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />} {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <pre className="whitespace-pre-wrap rounded bg-black p-3 font-sans text-[13px] leading-relaxed text-neutral-100">{draft}</pre>
          </>
        ) : (
          <div className="text-sm text-neutral-400">Draft not available in the recent event window.</div>
        )}
      </div>
    );
  }
  if (job.queue === "ARCHIVED") {
    const ev = latestEvent(job, events, "alert");
    return (
      <div className="rounded-lg border-2 border-amber-400 bg-amber-950/20 p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-bold text-amber-300">
          <Archive className="h-4 w-4" /> Autonomous action: archived in tracker
          {ev?.simulated && <span className="rounded bg-amber-400 px-1.5 text-[10px] font-bold text-amber-950">SIMULATED</span>}
        </div>
        <div className="text-[13px] leading-relaxed text-neutral-100">{ev ? ev.message : "Archived. Alert text not in the recent event window."}</div>
      </div>
    );
  }
  return null;
}

// ---------------------------------------------------------------- pane
const SCORING_PREFIXES = ["open_confidence", "apply_score", "Context", "Conflict"];

function recTone(rec: string): string {
  if (rec.includes("Immediately")) return "text-emerald-300";
  if (rec.includes("Caution")) return "text-amber-300";
  return "text-red-300";
}

export default function TracePane({ job, events }: { job: JobView | null; events: LiveWireEvent[] }) {
  if (!job) {
    return <div className="flex h-full items-center justify-center p-8 text-sm text-neutral-500">Select a watched job to see its auditable 3-agent trace.</div>;
  }
  const badge = badgeFor(job);
  const f = job.last_finder;
  const e = job.last_evidence;
  const v = job.last_verdict;
  const scoring = (v?.reasoning_with_citations ?? []).filter((c) => SCORING_PREFIXES.some((p) => c.claim.startsWith(p)));
  const actionLines = (v?.autonomous_action_taken ?? "").split("; ").filter(Boolean);

  return (
    <div className="space-y-4 p-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-extrabold text-white">{job.role}</h2>
          <span className="text-neutral-400">@ {job.company}</span>
          <StatusBadge kind={badge.kind} label={badge.label} />
          {job.is_demo && <span className="rounded bg-neutral-700 px-1.5 py-0.5 text-[10px] font-semibold text-neutral-200">DEMO SEED (synthetic snapshot)</span>}
        </div>
        <a href={job.url} target="_blank" rel="noreferrer" className="mt-0.5 inline-flex items-center gap-1 break-all font-mono text-[11px] text-neutral-400 hover:text-white">
          {hostOf(job.url)} <ExternalLink className="h-3 w-3" />
        </a>
        {v && <div className={`mt-2 text-xl font-extrabold ${recTone(v.recommendation)}`}>{v.recommendation}</div>}
      </div>

      <ActionCard job={job} events={events} />

      <AgentSection n={1} title="Finder Agent" powered="You.com Search + Research" icon={<Search className="h-4 w-4" />}>
        <Receipts items={f?.receipts ?? []} empty="No Finder receipts recorded." />
        <ActionLines lines={f?.actions_taken ?? []} />
      </AgentSection>

      <AgentSection n={2} title="Verifier Agent" powered="OpenCrawl hosted MCP + You.com Contents" icon={<Globe className="h-4 w-4" />}>
        <Receipts items={e?.receipts ?? []} empty="No Verifier receipts recorded." />
        {e && (
          <div className="font-mono text-[11px] text-neutral-400">
            {e.opencrawl_session_id ? `OpenCrawl agent session: ${e.opencrawl_session_id}` : "No OpenCrawl session id for this snapshot"} · DOM source: {e.fetch_engine ?? "none"}
          </div>
        )}
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <UrlDiffCard job={job} />
          <ApplyCard job={job} />
          <TimestampCard job={job} />
          <UncertainCard job={job} />
        </div>
        <ActionLines lines={e?.actions_taken ?? []} />
      </AgentSection>

      <AgentSection n={3} title="Scorer Agent" powered="Deterministic rules, no LLM" icon={<Calculator className="h-4 w-4" />}>
        {v ? (
          <>
            <div className="flex gap-5">
              <Gauge label="Open confidence" value={v.open_confidence} />
              <Gauge label="Apply score" value={v.apply_score} />
            </div>
            <Receipts items={scoring} empty="No scoring steps recorded." />
          </>
        ) : (
          <div className="text-sm text-neutral-500">No verdict yet.</div>
        )}
        <ActionLines lines={actionLines} />
      </AgentSection>
    </div>
  );
}
