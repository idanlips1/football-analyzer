import type { Job } from "../types";

const PROGRESS_LABELS: Record<string, string> = {
  starting: "Starting...",
  loading_events: "Loading match events...",
  interpreting_query: "Understanding your request...",
  filtering: "Finding matching moments...",
  building_clips: "Building highlights...",
};

interface Props {
  job: Job | null;
  isLoading: boolean;
  onRetry: () => void;
}

export default function VideoPlayer({ job, isLoading, onRetry }: Props) {
  // State 1: Empty
  if (!job && !isLoading) {
    return (
      <div className="bg-bg-card rounded-xl aspect-video flex items-center justify-center">
        <div className="text-center">
          <div className="text-4xl text-text-muted mb-3">⚽</div>
          <p className="text-text-muted text-sm">Select a match and ask for highlights</p>
        </div>
      </div>
    );
  }

  // State 2: Loading
  if (isLoading || (job && (job.status === "queued" || job.status === "processing"))) {
    const stage = job?.progress ?? "starting";
    const label = PROGRESS_LABELS[stage] ?? stage;
    return (
      <div className="bg-bg-card rounded-xl aspect-video flex flex-col items-center justify-center">
        <div className="w-9 h-9 border-3 border-accent/20 border-t-accent rounded-full animate-spin mb-4" />
        <p className="text-text-primary text-sm font-semibold">Generating highlights...</p>
        <p className="text-text-muted text-xs mt-1">{label}</p>
      </div>
    );
  }

  // State 4: Error
  if (job?.status === "failed") {
    return (
      <div className="bg-bg-card rounded-xl aspect-video flex flex-col items-center justify-center">
        <div className="text-3xl mb-3">⚠️</div>
        <p className="text-text-primary text-sm font-semibold mb-1">Something went wrong</p>
        <p className="text-text-muted text-xs mb-4 max-w-md text-center px-4">
          {job.error ?? "An unknown error occurred"}
        </p>
        <button
          onClick={onRetry}
          className="bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
        >
          Try again
        </button>
      </div>
    );
  }

  // State 3: Ready
  if (job?.status === "completed" && job.result) {
    return (
      <div className="bg-bg-card rounded-xl overflow-hidden">
        <video
          key={job.result.download_url}
          src={job.result.download_url}
          controls
          preload="metadata"
          className="w-full aspect-video"
        />
      </div>
    );
  }

  return null;
}
