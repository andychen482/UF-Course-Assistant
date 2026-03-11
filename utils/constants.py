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

ALLOWED_ORIGINS: list[str] = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
