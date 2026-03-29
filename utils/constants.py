"""
Centralised constants and environment-driven configuration.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")

# ---------------------------------------------------------------------------
# AWS Cognito
# ---------------------------------------------------------------------------

COGNITO_REGION: str = os.environ.get("COGNITO_REGION", "")
COGNITO_USER_POOL_ID: str = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_APP_CLIENT_ID: str = os.environ.get("COGNITO_APP_CLIENT_ID", "")

JWKS_TTL_SECONDS: int = 3600  # re-fetch signing keys once per hour

# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

SESSION_TTL_SECONDS: int = 30 * 60  # 30 minutes

# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------

CHAT_TABLE: str = "ufscheduler-chat"
USERS_TABLE: str = "ufscheduler-users"
METRICS_TABLE: str = "ufscheduler-stats"

MESSAGES_BATCH_SIZE: int = 20
MESSAGE_MAX_LENGTH: int = 250

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

_raw_origins = os.environ.get("ALLOWED_ORIGINS", "")
if not _raw_origins:
    raise RuntimeError(
        "ALLOWED_ORIGINS environment variable is required. "
        "Set it to a comma-separated list of allowed origins "
        "(e.g. 'http://localhost:3000,https://ufscheduler.com')."
    )
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ---------------------------------------------------------------------------
# Assistant intro (seeded as the first message in every new conversation)
# ---------------------------------------------------------------------------

INTRO_MESSAGE: str = (
    "Hey there, Gator! I'm your UF Course Assistant. Here's what I can help you with:\n"
    "\n"
    "- **Course Search** -- Find UF courses by code (e.g. \"COP3530\") or by topic "
    "(e.g. \"Data Structures\")\n"
    "- **Section Details** -- Look up instructors, meeting times, locations, and "
    "delivery mode for any course\n"
    "- **Professor Ratings** -- Pull up RateMyProfessors ratings, difficulty scores, "
    "and student reviews\n"
    "- **GatorEvals** -- Look up official UF teaching evaluation scores "
    "(1-5 scale, where 1 = Strongly Disagree and 5 = Strongly Agree)\n"
    "- **Reddit Opinions** -- Search r/UFL for real student experiences and opinions\n"
    "- **Scheduler Actions** -- Add or remove courses from the scheduler\n"
    "\n"
    "What can I help you with?"
)
