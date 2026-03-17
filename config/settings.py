"""Global pipeline settings: thresholds, weights, and defaults."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

PIPELINE_WORKSPACE = PROJECT_ROOT / "pipeline_workspace"

# Stage 1 — Ingestion
MIN_DURATION_SECONDS = 20 * 60  # 20 minutes

# Stage 2 — Transcription
ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")
# Speakers with at least this fraction of the top speaker's time are
# considered commentators (e.g., 0.3 = 30% of the top speaker's time).
COMMENTATOR_TIME_RATIO = 0.3

# Target highlights length
DEFAULT_HIGHLIGHTS_DURATION_SECONDS = 120
