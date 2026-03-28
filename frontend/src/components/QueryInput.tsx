import { useState } from "react";

const SUGGESTIONS = [
  "Full summary",
  "Just goals",
  "Cards & VAR",
  "Second half",
];

interface Props {
  disabled: boolean;
  onSubmit: (query: string) => void;
}

export default function QueryInput({ disabled, onSubmit }: Props) {
  const [query, setQuery] = useState("");

  function handleSubmit() {
    const trimmed = query.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setQuery("");
  }

  function handleChip(text: string) {
    onSubmit(text);
    setQuery("");
  }

  return (
    <div>
      <div className={`bg-surface-input border border-border-input rounded-xl px-4 py-3 flex items-center gap-3 ${disabled ? "opacity-50" : ""}`}>
        <span className="text-accent text-sm">✨</span>
        <input
          type="text"
          placeholder='Ask for highlights... "goals and penalties", "Salah moments"'
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          disabled={disabled}
          className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
        />
        <button
          onClick={handleSubmit}
          disabled={disabled || !query.trim()}
          className="bg-accent hover:bg-accent/80 disabled:opacity-40 text-white px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors"
        >
          Generate
        </button>
      </div>
      <div className="flex gap-1.5 mt-2 flex-wrap">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => handleChip(s)}
            disabled={disabled}
            className="bg-surface-input border border-border-input text-text-secondary px-2.5 py-1 rounded-full text-[10px] hover:border-border-input hover:text-text-primary disabled:opacity-40 transition-colors"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
