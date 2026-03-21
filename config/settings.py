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

# Stage 3 — Excitement analysis (OpenAI fallback)
# If Azure settings are not provided, Stage 3 can fall back to OpenAI directly.
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
# Model name for OpenAI chat completions when using OPENAI_API_KEY.
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

# Scoring weights (must sum to 1.0 when scaled consistently)
EXCITEMENT_ENERGY_WEIGHT: float = 0.10
EXCITEMENT_KEYWORD_WEIGHT: float = 0.20
EXCITEMENT_LLM_WEIGHT: float = 0.70

# Threshold for include_in_highlights (scale 0–10)
EXCITEMENT_THRESHOLD: float = 4.5

# Hard floor: entries with LLM score below this are excluded regardless of keywords/energy
EXCITEMENT_LLM_FLOOR: float = 4.0

# Duration penalty: utterances longer than onset seconds lose points at this rate.
# penalty = max(0, (duration_s - onset) / 30) * rate
# e.g. 90s utterance → (90-30)/30 * 1.5 = 3.0 pts deducted
EXCITEMENT_DURATION_PENALTY_ONSET: float = 30.0
EXCITEMENT_DURATION_PENALTY_RATE: float = 1.5

# LLM batching
EXCITEMENT_BATCH_SIZE: int = 20

# Target highlights length (10 minutes)
DEFAULT_HIGHLIGHTS_DURATION_SECONDS: float = 600.0

# Fade duration applied to each clip (seconds); 0.0 disables fade (stream-copy mode)
FADE_DURATION_SECONDS: float = 0.5

# Merged clip duration cap — clips longer than this are split during merging
MAX_CLIP_DURATION_SECONDS: float = 45.0

# Merge gap — consecutive excited utterances within this window are joined
MERGE_GAP_SECONDS: float = 5.0

# API-Football (api-sports.io) — header-based auth with x-rapidapi-key
API_FOOTBALL_KEY: str = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE_URL: str = "https://v3.football.api-sports.io"
