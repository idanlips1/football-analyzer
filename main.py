"""Entry point for the Football Highlights Generator pipeline."""

from __future__ import annotations

import argparse
import sys

from models.events import EventType
from pipeline.edr import EDRError, build_edr
from pipeline.excitement import ExcitementError, analyze_excitement
from pipeline.filtering import FilteringError, filter_edr
from pipeline.ingestion import IngestionError, ingest
from pipeline.transcription import TranscriptionError, transcribe
from pipeline.video import VideoError, build_highlights

# Highlight level → event types to include.
# Empty list = pass-through (include all event types).
HIGHLIGHT_LEVELS: dict[str, list[EventType]] = {
    "essential": [
        EventType.GOAL,
        EventType.PENALTY,
        EventType.RED_CARD,
        EventType.CELEBRATION,
    ],
    "standard": [
        EventType.GOAL,
        EventType.PENALTY,
        EventType.RED_CARD,
        EventType.CELEBRATION,
        EventType.NEAR_MISS,
        EventType.SAVE,
        EventType.VAR_REVIEW,
        EventType.SHOT_ON_TARGET,
    ],
    "extended": [
        EventType.GOAL,
        EventType.PENALTY,
        EventType.RED_CARD,
        EventType.CELEBRATION,
        EventType.NEAR_MISS,
        EventType.SAVE,
        EventType.VAR_REVIEW,
        EventType.SHOT_ON_TARGET,
        EventType.COUNTER_ATTACK,
        EventType.FREE_KICK,
        EventType.YELLOW_CARD,
        EventType.CARD,
    ],
    "full": [],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate football highlights from a YouTube match video.",
    )
    parser.add_argument("url", help="YouTube URL of the full match video")
    parser.add_argument(
        "--level",
        choices=list(HIGHLIGHT_LEVELS),
        default="standard",
        help="Highlight level: essential/standard/extended/full (default: standard)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-run all stages, ignoring cached outputs",
    )
    args = parser.parse_args()

    event_types = HIGHLIGHT_LEVELS[args.level]

    try:
        print("[1/5] Ingesting video...")
        metadata = ingest(args.url)
        duration_min = metadata["duration_seconds"] / 60
        print(f"      Video ID: {metadata['video_id']} ({duration_min:.0f} min)")

        print("[2/5] Transcribing commentary...")
        transcription = transcribe(metadata)
        utterance_count = len(transcription.get("utterances", []))
        print(f"      {utterance_count} utterances transcribed")

        print("[3/5] Analysing commentator excitement...")
        excitement = analyze_excitement(transcription, metadata)
        included = sum(1 for e in excitement if e.get("include_in_highlights"))
        print(f"      {len(excitement)} segments analysed, {included} flagged for highlights")

        print("[4/5] Building EDR and selecting clips...")
        edr = build_edr({"video_id": metadata["video_id"]})
        filtered = filter_edr(edr, event_types)
        print(
            f"      {filtered['clip_count']} clips selected"
            f" ({filtered['total_duration_display']} total)"
        )

        print("[5/5] Cutting clips and assembling highlights reel...")
        result = build_highlights(filtered, overwrite=args.overwrite)

        print(f"\nDone! Highlights saved to:\n  {result['highlights_path']}")
        print(f"  {result['clip_count']} clips · {result['total_duration_display']} total")

    except (
        IngestionError,
        TranscriptionError,
        ExcitementError,
        EDRError,
        FilteringError,
        VideoError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
