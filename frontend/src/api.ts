import type { Match, Job, JobCreateResponse } from "./types";

const BASE = "/api/v1";

export async function fetchMatches(): Promise<Match[]> {
  const res = await fetch(`${BASE}/matches`);
  if (!res.ok) throw new Error(`Failed to fetch matches: ${res.status}`);
  const data = await res.json();
  return data.matches;
}

export async function createJob(
  matchId: string,
  highlightsQuery: string
): Promise<JobCreateResponse> {
  const res = await fetch(`${BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      match_id: matchId,
      highlights_query: highlightsQuery,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.error?.message ?? `Job creation failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await fetch(`${BASE}/jobs/${jobId}`);
  if (!res.ok) throw new Error(`Failed to fetch job: ${res.status}`);
  return res.json();
}
