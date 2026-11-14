import type { JobsResponse, PollResponse, Scenario, SimulateResponse, VerifyResponse } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function describeDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; errors?: unknown };
    const errs = Array.isArray(d.errors) ? ` (${d.errors.join("; ")})` : "";
    if (typeof d.message === "string") return d.message + errs;
    return JSON.stringify(detail);
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new Error(`Cannot reach the backend at ${API_BASE}. Is uvicorn running?`);
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      detail = body.detail ?? body;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(describeDetail(detail, `HTTP ${res.status}`));
  }
  return (await res.json()) as T;
}

export const getJobs = () => request<JobsResponse>("/api/jobs");
export const verifyUrl = (url: string) =>
  request<VerifyResponse>("/api/verify", { method: "POST", body: JSON.stringify({ url }) });
export const pollNow = () => request<PollResponse>("/api/poll-now", { method: "POST" });
export const simulate = (scenario: Scenario) =>
  request<SimulateResponse>("/api/simulate-event", { method: "POST", body: JSON.stringify({ scenario }) });
export const resetDemo = () => request<{ ok: boolean; jobs: number }>("/api/reset-demo", { method: "POST" });
export const streamUrl = () => `${API_BASE}/api/stream`;
