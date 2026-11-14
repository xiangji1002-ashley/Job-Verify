"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, FlaskConical, Loader2, Radio, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";
import type { Scenario } from "@/lib/types";

export type Busy = "verify" | "poll" | "sim" | null;

const SCENARIOS: Array<{ id: Scenario; label: string }> = [
  { id: "early_window_drop", label: "Simulate Fresh Ashby Drop" },
  { id: "ghost_conflict_closed", label: "Simulate Source Closure Conflict" },
  { id: "breaking_hiring_freeze", label: "Simulate Breaking Hiring Freeze" },
];

interface Props {
  url: string;
  onUrl: (v: string) => void;
  onVerify: () => void;
  onPoll: () => void;
  onSimulate: (s: Scenario) => void;
  onReset: () => void;
  busy: Busy;
  connected: boolean;
}

export default function TopBar({ url, onUrl, onVerify, onPoll, onSimulate, onReset, busy, connected }: Props) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-neutral-700 bg-black px-4 py-3">
      <div className="flex items-center gap-2 pr-2">
        <ShieldCheck className="h-6 w-6 text-emerald-400" />
        <div className="leading-tight">
          <div className="text-base font-extrabold tracking-tight text-white">JobVerify</div>
          <div className="text-[10px] font-semibold uppercase tracking-widest text-neutral-400">Live Wire Command Center</div>
        </div>
      </div>

      <form
        className="flex min-w-[280px] flex-1 items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (url.trim() && !busy) onVerify();
        }}
      >
        <input
          value={url}
          onChange={(e) => onUrl(e.target.value)}
          placeholder="Paste any LinkedIn, Indeed, or ATS job link to verify live..."
          className="h-10 w-full rounded-md border border-neutral-600 bg-neutral-950 px-3 text-sm text-white placeholder:text-neutral-500 focus:border-emerald-400 focus:outline-none"
        />
        <button
          type="submit"
          disabled={!url.trim() || busy !== null}
          className="flex h-10 shrink-0 items-center gap-2 rounded-md bg-emerald-400 px-4 text-sm font-bold text-emerald-950 hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy === "verify" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
          Verify Live
        </button>
      </form>

      <button
        onClick={onPoll}
        disabled={busy !== null}
        className="flex h-10 items-center gap-2 rounded-md border border-neutral-500 px-3 text-sm font-semibold text-white hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {busy === "poll" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
        Poll All Now
      </button>

      <div className="relative" ref={menuRef}>
        <button
          onClick={() => setOpen((v) => !v)}
          disabled={busy !== null}
          className="flex h-10 items-center gap-1.5 rounded-md border border-dashed border-neutral-600 px-3 text-xs font-medium text-neutral-300 hover:border-neutral-400 hover:text-white disabled:opacity-40"
        >
          {busy === "sim" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
          Demo Event Simulator
          <ChevronDown className="h-3.5 w-3.5" />
        </button>
        {open && (
          <div className="absolute right-0 z-20 mt-1 w-72 overflow-hidden rounded-md border border-neutral-600 bg-neutral-950 shadow-2xl">
            <div className="border-b border-neutral-800 px-3 py-2 text-[10px] font-semibold uppercase tracking-widest text-amber-300">
              Simulated deltas - labelled [SIMULATED] everywhere
            </div>
            {SCENARIOS.map((s) => (
              <button
                key={s.id}
                onClick={() => {
                  setOpen(false);
                  onSimulate(s.id);
                }}
                className="block w-full px-3 py-2.5 text-left text-sm text-neutral-100 hover:bg-neutral-800"
              >
                {s.label}
              </button>
            ))}
            <button
              onClick={() => {
                setOpen(false);
                onReset();
              }}
              className="flex w-full items-center gap-2 border-t border-neutral-800 px-3 py-2.5 text-left text-sm text-neutral-400 hover:bg-neutral-800 hover:text-white"
            >
              <RotateCcw className="h-3.5 w-3.5" /> Reset demo data
            </button>
          </div>
        )}
      </div>

      <div className="flex items-center gap-1.5 text-xs font-semibold" title="Server-sent event stream from /api/stream">
        <Radio className={`h-4 w-4 ${connected ? "animate-pulse text-emerald-400" : "text-neutral-600"}`} />
        <span className={connected ? "text-emerald-400" : "text-neutral-500"}>{connected ? "LIVE" : "OFFLINE"}</span>
      </div>
    </header>
  );
}
