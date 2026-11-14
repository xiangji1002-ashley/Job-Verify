"use client";

import type { ReactNode } from "react";
import { AlertTriangle, Archive, CheckCircle2, FileText, Info, Send, Sprout, Star, Webhook, XCircle, Zap } from "lucide-react";
import { fmtClock } from "@/lib/derive";
import type { LiveWireEvent } from "@/lib/types";

const KIND: Record<string, { icon: ReactNode; tone: string }> = {
  seed: { icon: <Sprout className="h-4 w-4" />, tone: "text-neutral-400" },
  checked: { icon: <CheckCircle2 className="h-4 w-4" />, tone: "text-neutral-300" },
  status_change: { icon: <Zap className="h-4 w-4" />, tone: "text-sky-300" },
  promoted: { icon: <Star className="h-4 w-4" />, tone: "text-lime-300" },
  outreach_draft: { icon: <FileText className="h-4 w-4" />, tone: "text-lime-200" },
  webhook: { icon: <Webhook className="h-4 w-4" />, tone: "text-violet-300" },
  archived: { icon: <Archive className="h-4 w-4" />, tone: "text-amber-300" },
  alert: { icon: <AlertTriangle className="h-4 w-4" />, tone: "text-red-400" },
  error: { icon: <XCircle className="h-4 w-4" />, tone: "text-red-400" },
  simulated: { icon: <Send className="h-4 w-4" />, tone: "text-amber-300" },
};

export default function EventFeed({ events, onSelectJob }: { events: LiveWireEvent[]; onSelectJob: (id: number) => void }) {
  if (events.length === 0) {
    return <div className="p-4 text-sm text-neutral-500">No autonomous actions yet.</div>;
  }
  return (
    <ul className="divide-y divide-neutral-900">
      {events.map((e, i) => {
        const k = KIND[e.kind] ?? { icon: <Info className="h-4 w-4" />, tone: "text-neutral-400" };
        const clickable = typeof e.job_id === "number";
        return (
          <li key={e.id ?? `${e.at}-${i}`} className="flex gap-2.5 px-4 py-2">
            <span className={`mt-0.5 shrink-0 ${k.tone}`}>{k.icon}</span>
            <div className="min-w-0 flex-1">
              <button
                disabled={!clickable}
                onClick={() => clickable && onSelectJob(e.job_id as number)}
                className={`block text-left text-[13px] leading-snug ${k.tone} ${clickable ? "hover:underline" : "cursor-default"}`}
              >
                {e.message}
              </button>
            </div>
            <span className="shrink-0 font-mono text-[10px] text-neutral-600">{fmtClock(e.at)}</span>
          </li>
        );
      })}
    </ul>
  );
}
