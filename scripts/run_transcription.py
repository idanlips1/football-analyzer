"""Run Stage 2 (audio extraction + transcription) on an already-ingested video.

Usage:
    python scripts/run_transcription.py <video_id>

Example:
    python scripts/run_transcription.py cTqY53zWypk
"""

import json
import sys
from pathlib import Path

# Allow imports from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import PIPELINE_WORKSPACE
from pipeline.transcription import transcribe
from utils.logger import setup_logging


def main() -> None:
    setup_logging()

    if len(sys.argv) < 2:
        print("Usage: python scripts/run_transcription.py <video_id>")
        print()
        # List available videos
        available = [
            d.name
            for d in PIPELINE_WORKSPACE.iterdir()
            if d.is_dir() and (d / "metadata.json").exists()
        ]
        if available:
            print("Available videos:")
            for vid in available:
                meta = json.loads((PIPELINE_WORKSPACE / vid / "metadata.json").read_text())
                print(f"  {vid}  ({meta.get('video_filename', '?')})")
        else:
            print("No ingested videos found. Run Stage 1 first.")
        sys.exit(1)

    video_id = sys.argv[1]
    metadata_path = PIPELINE_WORKSPACE / video_id / "metadata.json"

    if not metadata_path.exists():
        print(f"Error: No metadata found at {metadata_path}")
        sys.exit(1)

    metadata = json.loads(metadata_path.read_text())

    print(f"Video:    {metadata['video_filename']}")
    print(f"Duration: {metadata['duration_seconds'] / 60:.1f} minutes")
    print()
    print("Stage 2 — Audio extraction + AssemblyAI transcription")
    print("  Audio extraction takes ~2-3 minutes")
    print("  AssemblyAI transcription takes ~15-30 minutes for a full match")
    print()

    result = transcribe(metadata)

    print()
    print("=== DONE ===")
    print(f"Total utterances:     {result['total_utterances']}")
    print(f"Commentator speakers: {result['commentator_speakers']}")
    print()
    print("First 5 utterances:")
    for u in result["utterances"][:5]:
        minutes = u["start"] // 60000
        seconds = (u["start"] % 60000) // 1000
        print(f"  [{u['speaker']}] {minutes:02d}:{seconds:02d}  {u['text'][:100]}")


if __name__ == "__main__":
    main()
