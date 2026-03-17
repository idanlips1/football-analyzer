"""Run Stage 3 (excitement) + Stage 4 (EDR) for an already-ingested video.

This script does not require command-line arguments. It prompts for a video_id
and then runs:
  - Stage 3: pipeline.excitement.analyze_excitement(...)
  - Stage 4: pipeline.edr.build_edr(...)

Outputs are written into pipeline_workspace/<video_id>/:
  - excitement.json
  - edr.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow imports from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import PIPELINE_WORKSPACE  # noqa: E402
from pipeline.edr import build_edr  # noqa: E402
from pipeline.excitement import analyze_excitement  # noqa: E402
from utils.logger import setup_logging  # noqa: E402


def _list_available_video_ids() -> list[str]:
    if not PIPELINE_WORKSPACE.exists():
        return []
    return sorted(
        d.name
        for d in PIPELINE_WORKSPACE.iterdir()
        if d.is_dir() and (d / "metadata.json").exists()
    )


def main() -> None:
    setup_logging()

    available = _list_available_video_ids()
    if available:
        print("Available video_ids:")
        for vid in available:
            print(f"  - {vid}")
        print()

    video_id = input("Enter video_id to run Stage 3+4: ").strip()
    if not video_id:
        print("No video_id provided.")
        raise SystemExit(1)

    ws = PIPELINE_WORKSPACE / video_id
    metadata_path = ws / "metadata.json"
    transcription_path = ws / "transcription.json"

    if not metadata_path.exists():
        print(f"Missing {metadata_path}. Run Stage 1 first.")
        raise SystemExit(1)
    if not transcription_path.exists():
        print(f"Missing {transcription_path}. Run Stage 2 first.")
        raise SystemExit(1)

    metadata = json.loads(metadata_path.read_text())
    transcription = json.loads(transcription_path.read_text())

    print()
    print("Running Stage 3 (excitement)…")
    excitement_entries = analyze_excitement(transcription, metadata)
    print(f"Stage 3 done: {len(excitement_entries)} entries")
    print(f"Wrote: {ws / 'excitement.json'}")

    print()
    print("Running Stage 4 (EDR)…")
    edr = build_edr({"video_id": video_id})
    print(f"Stage 4 done: {edr['clip_count']} clips, total {edr['total_duration_seconds']:.1f}s")
    print(f"Wrote: {ws / 'edr.json'}")


if __name__ == "__main__":
    main()
