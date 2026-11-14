"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, ListChecks, X } from "lucide-react";
import EventFeed from "@/components/EventFeed";
import TopBar, { type Busy } from "@/components/TopBar";
import TracePane from "@/components/TracePane";
import Watchlist from "@/components/Watchlist";
import { getJobs, pollNow, resetDemo, simulate, streamUrl, verifyUrl } from "@/lib/api";
import type { JobView, LiveWireEvent, Scenario } from "@/lib/types";

interface Toast {
  tone: "error" | "info";
  text: string;
}

export default function Home() {
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [events, setEvents] = useState<LiveWireEvent[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [connected, setConnected] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const [flashIds, setFlashIds] = useState<Set<number>>(new Set());
  const seenUpdates = useRef<Map<number, string>>(new Map());
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getJobs();
      const changed = new Set<number>();
      for (const j of data.jobs) {
        const prev = seenUpdates.current.get(j.id);
        if (prev !== undefined && prev !== j.updated_at) changed.add(j.id);
        seenUpdates.current.set(j.id, j.updated_at);
      }
      setJobs(data.jobs);
      setEvents(data.events);
      setSelectedId((cur) => (cur !== null && data.jobs.some((j) => j.id === cur) ? cur : data.jobs[0]?.id ?? null));
      if (changed.size > 0) {
        setFlashIds(changed);
        setTimeout(() => setFlashIds(new Set()), 2200);
      }
    } catch (err) {
      setToast({ tone: "error", text: err instanceof Error ? err.message : "Failed to load jobs" });
    }
  }, []);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => void refresh(), 250);
  }, [refresh]);

  // initial load + slow fallback poll
  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 15000);
    return () => clearInterval(t);
  }, [refresh]);

  // real-time Server-Sent Events from the Live Wire watcher
  useEffect(() => {
    const es = new EventSource(streamUrl());
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false); // EventSource reconnects on its own
    es.onmessage = (msg: MessageEvent<string>) => {
      try {
        const ev = JSON.parse(msg.data) as LiveWireEvent;
        setEvents((prev) => (ev.id != null && prev.some((p) => p.id === ev.id) ? prev : [ev, ...prev].slice(0, 80)));
      } catch {
        /* ignore malformed frame */
      }
      scheduleRefresh();
    };
    return () => es.close();
  }, [scheduleRefresh]);

  async function run<T>(kind: Exclude<Busy, null>, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(kind);
    setToast(null);
    try {
      return await fn();
    } catch (err) {
      setToast({ tone: "error", text: err instanceof Error ? err.message : "Request failed" });
      return undefined;
    } finally {
      setBusy(null);
    }
  }

  const onVerify = async () => {
    const res = await run("verify", () => verifyUrl(url.trim()));
    if (res) {
      setUrl("");
      if (res.job) setSelectedId(res.job.id);
      await refresh();
    }
  };

  const onPoll = async () => {
    const res = await run("poll", pollNow);
    if (res) {
      const skipped = res.results.filter((r) => r.outcome === "skipped_demo").length;
      setToast({
        tone: "info",
        text: `Polled ${res.results.length} job(s)` + (skipped ? `; ${skipped} seeded demo job(s) skipped (use the simulator)` : ""),
      });
      await refresh();
    }
  };

  const onSimulate = async (scenario: Scenario) => {
    const res = await run("sim", () => simulate(scenario));
    if (res) {
      if (res.job) setSelectedId(res.job.id);
      await refresh();
    }
  };

  const onReset = async () => {
    const res = await run("sim", resetDemo);
    if (res) {
      seenUpdates.current.clear();
      setSelectedId(null);
      await refresh();
    }
  };

  const selected = jobs.find((j) => j.id === selectedId) ?? null;
  const priority = jobs.filter((j) => j.queue === "PRIORITY").length;
  const archived = jobs.filter((j) => j.queue === "ARCHIVED").length;

  return (
    <div className="flex h-screen flex-col bg-black text-neutral-100">
      <TopBar
        url={url}
        onUrl={setUrl}
        onVerify={() => void onVerify()}
        onPoll={() => void onPoll()}
        onSimulate={(s) => void onSimulate(s)}
        onReset={() => void onReset()}
        busy={busy}
        connected={connected}
      />

      {toast && (
        <div className={`flex items-center gap-2 px-4 py-2 text-sm font-medium ${toast.tone === "error" ? "bg-red-600 text-white" : "bg-sky-400 text-sky-950"}`}>
          {toast.tone === "error" && <AlertTriangle className="h-4 w-4" />}
          <span className="min-w-0 flex-1">{toast.text}</span>
          <button onClick={() => setToast(null)} aria-label="Dismiss">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <main className="grid min-h-0 flex-1 grid-cols-1 divide-neutral-700 lg:grid-cols-2 lg:divide-x">
        {/* LEFT: watchlist + autonomous event feed */}
        <section className="flex min-h-0 flex-col">
          <div className="flex items-center gap-2 border-b border-neutral-800 px-4 py-2">
            <ListChecks className="h-4 w-4 text-emerald-400" />
            <h2 className="text-sm font-bold text-white">Live Wire Watchlist</h2>
            <span className="ml-auto text-[11px] text-neutral-400">
              {jobs.length} watched · {priority} priority · {archived} archived
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Watchlist jobs={jobs} selectedId={selectedId} flashIds={flashIds} onSelect={setSelectedId} />
          </div>
          <div className="flex items-center gap-2 border-y border-neutral-800 bg-neutral-950 px-4 py-2">
            <Activity className="h-4 w-4 text-sky-300" />
            <h2 className="text-sm font-bold text-white">Autonomous Event Feed</h2>
            <span className="ml-auto text-[11px] text-neutral-500">newest first</span>
          </div>
          <div className="h-72 shrink-0 overflow-y-auto bg-neutral-950">
            <EventFeed events={events} onSelectJob={setSelectedId} />
          </div>
        </section>

        {/* RIGHT: auditable receipts + 3-agent trace */}
        <section className="min-h-0 overflow-y-auto bg-neutral-950">
          <TracePane job={selected} events={events} />
          <footer className="border-t border-neutral-800 px-4 py-3 text-[11px] text-neutral-500">
            Powered by You.com (Search, Contents, Research) and OpenCrawl (hosted MCP with agent session capture).
          </footer>
        </section>
      </main>
    </div>
  );
}
