import { useState } from "react";
import type { Match } from "../types";
import MatchCard from "./MatchCard";

interface Props {
  matches: Match[];
  selectedMatch: Match | null;
  onSelectMatch: (match: Match) => void;
  loading: boolean;
}

export default function Sidebar({ matches, selectedMatch, onSelectMatch, loading }: Props) {
  const [filter, setFilter] = useState("");

  const filtered = matches.filter((m) => {
    const q = filter.toLowerCase();
    return (
      m.title.toLowerCase().includes(q) ||
      m.home_team.toLowerCase().includes(q) ||
      m.away_team.toLowerCase().includes(q) ||
      m.competition.toLowerCase().includes(q)
    );
  });

  return (
    <aside className="w-72 flex-shrink-0 bg-surface border-r border-border-subtle p-5 flex flex-col h-screen">
      <div className="flex items-center gap-2.5 mb-6">
        <span className="text-2xl">⚽</span>
        <div>
          <div className="text-sm font-bold text-text-primary tracking-wide">MatchCut</div>
          <div className="text-[9px] text-text-muted uppercase tracking-widest">Football Highlights</div>
        </div>
      </div>

      <div className="relative mb-4">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm">🔍</span>
        <input
          type="text"
          placeholder="Search matches..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full bg-surface-input border border-border-input rounded-lg py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent"
        />
      </div>

      <div className="text-[9px] text-text-muted uppercase tracking-[0.15em] font-semibold mb-2.5">Matches</div>
      <div className="flex flex-col gap-1.5 flex-1 overflow-y-auto">
        {loading ? (
          <div className="text-text-muted text-sm animate-pulse py-4 text-center">Loading matches...</div>
        ) : filtered.length === 0 ? (
          <div className="text-text-muted text-sm py-4 text-center">
            {matches.length === 0 ? "No matches available" : "No matches found"}
          </div>
        ) : (
          filtered.map((m) => (
            <MatchCard
              key={m.match_id}
              match={m}
              selected={selectedMatch?.match_id === m.match_id}
              onClick={() => onSelectMatch(m)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
