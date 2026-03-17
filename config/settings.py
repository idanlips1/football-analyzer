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

# Stage 3 — Excitement analysis (Azure OpenAI)
AZURE_OPENAI_API_KEY: str = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT: str = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_VERSION: str = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZURE_OPENAI_DEPLOYMENT: str = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")

# Scoring weights (must sum to 1.0 when scaled consistently)
EXCITEMENT_ENERGY_WEIGHT: float = 0.25
EXCITEMENT_KEYWORD_WEIGHT: float = 0.35
EXCITEMENT_LLM_WEIGHT: float = 0.40

# Threshold for include_in_highlights (scale 0–10)
EXCITEMENT_THRESHOLD: float = 5.0

# LLM batching
EXCITEMENT_BATCH_SIZE: int = 20

# Target highlights length
DEFAULT_HIGHLIGHTS_DURATION_SECONDS = 120
