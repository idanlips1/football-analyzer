import type { Job } from "../types";

interface Props {
  job: Job;
  onDismiss: () => void;
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

export default function QueryStatus({ job, onDismiss }: Props) {
  if (job.status !== "completed" || !job.result) return null;

  return (
    <div className="bg-accent-surface border border-accent-border rounded-lg px-3.5 py-2.5 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className="text-accent text-xs">✓</span>
        <span className="text-text-primary text-xs">
          &ldquo;{job.highlights_query}&rdquo;
        </span>
        <span className="text-text-muted text-[11px]">
          · {job.result.clip_count} clips · {formatDuration(job.result.duration_seconds)}
        </span>
      </div>
      <button
        onClick={onDismiss}
        className="text-text-muted hover:text-text-secondary text-xs transition-colors"
      >
        ✕
      </button>
    </div>
  );
}
