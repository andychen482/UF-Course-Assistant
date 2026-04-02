"""
DynamoDB client.

Uses the ECS task IAM role for credentials (no explicit keys needed).
For local development, configure ``~/.aws/credentials`` or set
``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` env vars.
"""

from __future__ import annotations

import boto3

from utils.constants import (
    AI_CHATS_TABLE,
    CHAT_TABLE,
    COGNITO_REGION,
    METRICS_TABLE,
    USERS_TABLE,
)

_resource = boto3.resource("dynamodb", region_name=COGNITO_REGION)

chat_table = _resource.Table(CHAT_TABLE)
users_table = _resource.Table(USERS_TABLE)
metrics_table = _resource.Table(METRICS_TABLE)
ai_chats_table = _resource.Table(AI_CHATS_TABLE)
