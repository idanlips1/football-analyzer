import { useCallback, useEffect, useRef, useState } from "react";
import type { Job, Match } from "./types";
import { createJob, fetchJob, fetchMatches } from "./api";
import Sidebar from "./components/Sidebar";
import VideoPlayer from "./components/VideoPlayer";
import QueryInput from "./components/QueryInput";
import QueryStatus from "./components/QueryStatus";

const POLL_INTERVAL_MS = 3000;

export default function App() {
  const [matches, setMatches] = useState<Match[]>([]);
  const [matchesLoading, setMatchesLoading] = useState(true);
  const [selectedMatch, setSelectedMatch] = useState<Match | null>(null);
  const [currentJob, setCurrentJob] = useState<Job | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load matches on mount
  useEffect(() => {
    fetchMatches()
      .then(setMatches)
      .catch((err) => console.error("Failed to load matches:", err))
      .finally(() => setMatchesLoading(false));
  }, []);

  // Cleanup poll on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const cancelPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (jobId: string) => {
      cancelPoll();
      pollRef.current = setInterval(async () => {
        try {
          const job = await fetchJob(jobId);
          setCurrentJob(job);
          if (job.status === "completed" || job.status === "failed") {
            cancelPoll();
            setIsLoading(false);
          }
        } catch (err) {
          console.error("Poll error:", err);
        }
      }, POLL_INTERVAL_MS);
    },
    [cancelPoll]
  );

  function handleSelectMatch(match: Match) {
    cancelPoll();
    setSelectedMatch(match);
    setCurrentJob(null);
    setIsLoading(false);
  }

  async function handleSubmitQuery(query: string) {
    if (!selectedMatch) return;
    cancelPoll();
    setIsLoading(true);
    setCurrentJob(null);

    try {
      const res = await createJob(selectedMatch.match_id, query);

      if (res.status === "completed") {
        // Cache hit — fetch full job for download_url
        const job = await fetchJob(res.job_id);
        setCurrentJob(job);
        setIsLoading(false);
      } else {
        // Queued — start polling
        setCurrentJob({
          job_id: res.job_id,
          status: "queued",
          progress: null,
          match_id: selectedMatch.match_id,
          highlights_query: query,
          query: `${selectedMatch.match_id} — ${query}`,
          result: null,
          error: null,
          created_at: new Date().toISOString(),
        });
        startPolling(res.job_id);
      }
    } catch (err) {
      setCurrentJob({
        job_id: "",
        status: "failed",
        progress: null,
        match_id: selectedMatch.match_id,
        highlights_query: query,
        query: "",
        result: null,
        error: err instanceof Error ? err.message : "An unknown error occurred",
        created_at: new Date().toISOString(),
      });
      setIsLoading(false);
    }
  }

  function handleRetry() {
    setCurrentJob(null);
    setIsLoading(false);
  }

  const displayName = selectedMatch
    ? selectedMatch.home_team && selectedMatch.away_team
      ? `${selectedMatch.home_team} vs ${selectedMatch.away_team}`
      : selectedMatch.title
    : "";

  return (
    <div className="flex h-screen bg-bg">
      <Sidebar
        matches={matches}
        selectedMatch={selectedMatch}
        onSelectMatch={handleSelectMatch}
        loading={matchesLoading}
      />

      <main className="flex-1 p-6 flex flex-col overflow-hidden">
        {selectedMatch ? (
          <>
            {/* Match header */}
            <div className="flex items-center justify-between mb-5">
              <div>
                <h1 className="text-xl font-bold text-text-primary">
                  {displayName}
                </h1>
                <p className="text-text-muted text-xs mt-0.5">
                  {[selectedMatch.competition, selectedMatch.season_label]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              {currentJob?.status === "completed" && currentJob.result && (
                <a
                  href={currentJob.result.download_url}
                  download
                  className="bg-surface-input border border-border-input text-text-secondary px-3.5 py-1.5 rounded-md text-xs hover:text-text-primary transition-colors"
                >
                  ⬇ Download
                </a>
              )}
            </div>

            {/* Video player */}
            <div className="mb-4">
              <VideoPlayer
                job={currentJob}
                isLoading={isLoading}
                onRetry={handleRetry}
              />
            </div>

            {/* Query status banner */}
            {currentJob?.status === "completed" && currentJob.result && (
              <div className="mb-4">
                <QueryStatus
                  job={currentJob}
                  onDismiss={() => setCurrentJob(null)}
                />
              </div>
            )}

            {/* Query input — pushed to bottom */}
            <div className="mt-auto">
              <QueryInput
                disabled={isLoading || !selectedMatch}
                onSubmit={handleSubmitQuery}
              />
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <div className="text-5xl mb-4">⚽</div>
              <h2 className="text-lg font-semibold text-text-primary mb-1">
                Football Highlights
              </h2>
              <p className="text-text-muted text-sm">
                Pick a match from the sidebar to get started
              </p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
