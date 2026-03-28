export interface Match {
  match_id: string;
  title: string;
  home_team: string;
  away_team: string;
  competition: string;
  season_label: string;
}

export interface JobResult {
  download_url: string;
  duration_seconds: number;
  clip_count: number;
  expires_at: string;
}

export interface Job {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed";
  progress: string | null;
  match_id: string;
  highlights_query: string;
  query: string;
  result: JobResult | null;
  error: string | null;
  created_at: string;
}

export interface JobCreateResponse {
  job_id: string;
  status: string;
  poll_url: string;
}
