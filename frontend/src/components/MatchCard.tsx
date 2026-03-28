import type { Match } from "../types";

interface Props {
  match: Match;
  selected: boolean;
  onClick: () => void;
}

export default function MatchCard({ match, selected, onClick }: Props) {
  const displayName =
    match.home_team && match.away_team
      ? `${match.home_team} vs ${match.away_team}`
      : match.title;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-lg p-3 transition-colors ${
        selected
          ? "bg-accent-surface border border-accent-border"
          : "bg-surface border border-border-subtle hover:border-border-input"
      }`}
    >
      <div className={`text-sm font-semibold ${selected ? "text-text-primary" : "text-text-secondary"}`}>
        {displayName}
      </div>
      <div className="flex gap-1.5 mt-1">
        {match.competition && (
          <span className={`text-xs px-1.5 rounded ${selected ? "bg-accent-surface text-accent" : "bg-surface-input text-text-muted"}`}>
            {match.competition}
          </span>
        )}
        <span className="text-xs text-text-muted">{match.season_label}</span>
      </div>
    </button>
  );
}
