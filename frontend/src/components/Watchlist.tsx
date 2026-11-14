"use client";

import { Archive, Star } from "lucide-react";
import { Gauge, StatusBadge } from "@/components/Badges";
import { badgeFor, relTime } from "@/lib/derive";
import type { JobView } from "@/lib/types";

interface Props {
  jobs: JobView[];
  selectedId: number | null;
  flashIds: Set<number>;
  onSelect: (id: number) => void;
}

export default function Watchlist({ jobs, selectedId, flashIds, onSelect }: Props) {
  if (jobs.length === 0) {
    return <div className="p-6 text-sm text-neutral-400">No watched jobs yet. Paste a link above and press Verify Live.</div>;
  }
  return (
    <ul className="divide-y divide-neutral-800">
      {jobs.map((job) => {
        const badge = badgeFor(job);
        const v = job.last_verdict;
        const selected = job.id === selectedId;
        return (
          <li key={job.id}>
            <button
              onClick={() => onSelect(job.id)}
              className={`block w-full px-4 py-3 text-left transition-colors ${
                selected ? "bg-neutral-800 ring-1 ring-inset ring-emerald-400" : "hover:bg-neutral-900"
              } ${flashIds.has(job.id) ? "animate-pulse bg-neutral-800" : ""}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-bold text-white">{job.role}</div>
                  <div className="truncate text-xs text-neutral-400">
                    {job.company}
                    {job.is_demo && <span className="ml-2 rounded bg-neutral-700 px-1 text-[10px] font-semibold text-neutral-300">DEMO SEED</span>}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <StatusBadge kind={badge.kind} label={badge.label} />
                  <div className="flex items-center gap-1.5 text-[10px] text-neutral-500">
                    {job.queue === "PRIORITY" && (
                      <span className="flex items-center gap-0.5 font-semibold text-lime-300">
                        <Star className="h-3 w-3" /> PRIORITY
                      </span>
                    )}
                    {job.queue === "ARCHIVED" && (
                      <span className="flex items-center gap-0.5 font-semibold text-neutral-400">
                        <Archive className="h-3 w-3" /> ARCHIVED
                      </span>
                    )}
                    <span>{relTime(job.last_checked_at)}</span>
                  </div>
                </div>
              </div>
              {v ? (
                <div className="mt-2.5 flex gap-4">
                  <Gauge label="Open confidence" value={v.open_confidence} />
                  <Gauge label="Apply score" value={v.apply_score} />
                </div>
              ) : (
                <div className="mt-2 text-xs text-neutral-500">Awaiting first verdict</div>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
