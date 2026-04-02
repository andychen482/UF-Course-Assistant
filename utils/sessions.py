"""
DynamoDB-backed conversation session store.

Each session is a row in the ``ufscheduler-ai-chats`` table keyed by
``(user_sub, session_id)``.  Messages are stored as a list attribute on the
item, and a human-readable title is auto-generated from the first user
message.

This replaces the earlier in-memory store and works across multiple ECS
tasks without any shared state.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Key

from utils.constants import INTRO_MESSAGE
from utils.dynamo import ai_chats_table

logger = logging.getLogger("uvicorn.error")

_TITLE_MAX_LENGTH = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(user_sub: str) -> tuple[str, list[dict[str, str]]]:
    """Create a new conversation and return ``(session_id, messages)``."""
    session_id = uuid.uuid4().hex
    now = _now_iso()
    messages: list[dict[str, str]] = [
        {"role": "assistant", "content": INTRO_MESSAGE},
    ]
    ai_chats_table.put_item(Item={
        "user_sub": user_sub,
        "session_id": session_id,
        "title": "New Chat",
        "messages": messages,
        "created_at": now,
        "updated_at": now,
    })
    return session_id, messages


def get_session(session_id: str, user_sub: str) -> list[dict[str, str]] | None:
    """Return the message list for a session owned by *user_sub*.

    Returns ``None`` when the session does not exist or belongs to a
    different user (the composite key enforces ownership).
    """
    response = ai_chats_table.get_item(
        Key={"user_sub": user_sub, "session_id": session_id},
    )
    item = response.get("Item")
    if item is None:
        return None
    return item.get("messages", [])


def add_message(session_id: str, user_sub: str, role: str, content: str) -> None:
    """Append a message and update the timestamp.

    If this is the first user message the title is auto-set from the
    message content (truncated to *_TITLE_MAX_LENGTH* characters).
    """
    now = _now_iso()
    try:
        ai_chats_table.update_item(
            Key={"user_sub": user_sub, "session_id": session_id},
            UpdateExpression=(
                "SET #msgs = list_append(#msgs, :new_msg), "
                "#upd = :now"
            ),
            ExpressionAttributeNames={
                "#msgs": "messages",
                "#upd": "updated_at",
            },
            ExpressionAttributeValues={
                ":new_msg": [{"role": role, "content": content}],
                ":now": now,
            },
        )
    except Exception:
        logger.exception("Failed to append message to session %s", session_id)
        return

    if role == "user":
        _set_title_if_default(session_id, user_sub, content)


def _set_title_if_default(
    session_id: str, user_sub: str, first_message: str
) -> None:
    """Set the title only when it is still the placeholder 'New Chat'."""
    title = first_message.strip().replace("\n", " ")
    if len(title) > _TITLE_MAX_LENGTH:
        title = title[:_TITLE_MAX_LENGTH - 1] + "\u2026"
    try:
        ai_chats_table.update_item(
            Key={"user_sub": user_sub, "session_id": session_id},
            UpdateExpression="SET #t = :title",
            ConditionExpression="#t = :default",
            ExpressionAttributeNames={"#t": "title"},
            ExpressionAttributeValues={
                ":title": title,
                ":default": "New Chat",
            },
        )
    except ai_chats_table.meta.client.exceptions.ConditionalCheckFailedException:
        pass
    except Exception:
        logger.exception("Failed to update title for session %s", session_id)


def delete_session(session_id: str, user_sub: str) -> bool:
    """Delete a session. Returns ``True`` if it existed."""
    response = ai_chats_table.delete_item(
        Key={"user_sub": user_sub, "session_id": session_id},
        ReturnValues="ALL_OLD",
    )
    return "Attributes" in response


def list_sessions(user_sub: str) -> list[dict[str, Any]]:
    """Return summary info for all of a user's conversations.

    Each dict contains ``session_id``, ``title``, and ``updated_at``.
    Results are sorted newest-first by ``updated_at``.
    """
    response = ai_chats_table.query(
        KeyConditionExpression=Key("user_sub").eq(user_sub),
        ProjectionExpression="session_id, title, updated_at",
    )
    items: list[dict[str, Any]] = response.get("Items", [])

    while response.get("LastEvaluatedKey"):
        response = ai_chats_table.query(
            KeyConditionExpression=Key("user_sub").eq(user_sub),
            ProjectionExpression="session_id, title, updated_at",
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return items


def get_session_detail(
    session_id: str, user_sub: str
) -> dict[str, Any] | None:
    """Return the full conversation item (including messages) or ``None``."""
    response = ai_chats_table.get_item(
        Key={"user_sub": user_sub, "session_id": session_id},
    )
    return response.get("Item")
