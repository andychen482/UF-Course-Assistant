"""
Public chat room controller.

Replaces the Socket.io real-time chat from server.js with SSE receive + REST
send.  An in-process pub/sub fan-out broadcasts new messages and active-user
count changes to every connected SSE client.

NOTE: The subscriber set lives in process memory.  For multi-container ECS
deployments, swap the broadcast mechanism for Redis pub/sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Key
from fastapi import HTTPException, status

from api.models.chat_room import ChatMessage, LoadMessagesResponse
from utils.constants import MESSAGE_MAX_LENGTH, MESSAGES_BATCH_SIZE
from utils.dynamo import chat_table, metrics_table, users_table

logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# In-process pub/sub
# ---------------------------------------------------------------------------

_subscribers: set[asyncio.Queue[dict[str, str]]] = set()
_active_users: int = 0


def _subscribe() -> asyncio.Queue[dict[str, str]]:
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    _subscribers.add(queue)
    return queue


def _unsubscribe(queue: asyncio.Queue[dict[str, str]]) -> None:
    _subscribers.discard(queue)


async def _broadcast(event: dict[str, str]) -> None:
    for queue in _subscribers:
        await queue.put(event)


async def _broadcast_active_users() -> None:
    await _broadcast({
        "event": "active_users",
        "data": json.dumps({"active_users": _active_users}),
    })


def _update_daily_user_count() -> None:
    """Increment today's connection counter in the metrics table."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S.%fZ")

    try:
        metrics_table.update_item(
            Key={"type": f"UserCount-{today}", "name": "Connections"},
            UpdateExpression=(
                "SET #cnt = if_not_exists(#cnt, :zero) + :inc, "
                "lastUpdated = :time"
            ),
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={
                ":zero": 0,
                ":inc": 1,
                ":time": time_str,
            },
        )
    except Exception:
        logger.exception("Unable to update daily user count")


# ---------------------------------------------------------------------------
# SSE event stream
# ---------------------------------------------------------------------------


async def event_stream(user: dict[str, Any]) -> AsyncGenerator[dict[str, str], None]:
    """SSE generator: yields ``message`` and ``active_users`` events."""
    global _active_users
    
    queue = _subscribe()
    _active_users += 1
    _update_daily_user_count()
    await _broadcast_active_users()

    try:
        while True:
            event = await queue.get()
            yield event
    except asyncio.CancelledError:
        pass
    finally:
        _unsubscribe(queue)
        _active_users -= 1
        await _broadcast_active_users()


# ---------------------------------------------------------------------------
# Send message
# ---------------------------------------------------------------------------


async def send_message(message_text: str, user: dict[str, Any]) -> ChatMessage:
    """Validate, persist, and broadcast a chat message.

    The sender's username is looked up from DynamoDB using the JWT ``sub``
    claim -- the client cannot spoof it.
    """
    user_sub: str = user["sub"]

    if not message_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty",
        )
    message_text = message_text[:MESSAGE_MAX_LENGTH]

    user_record = users_table.get_item(Key={"sub": user_sub}).get("Item")
    if not user_record or "username" not in user_record:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must set a username before sending messages",
        )

    username = user_record["username"]
    timestamp = datetime.now(timezone.utc).isoformat()

    item = {
        "id": "General",
        "message": message_text,
        "user": username,
        "timestamp": timestamp,
    }

    try:
        chat_table.put_item(Item=item)
    except Exception:
        logger.exception("Unable to save chat message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error saving message",
        )

    await _broadcast({
        "event": "message",
        "data": json.dumps(item),
    })

    return ChatMessage(**item)


# ---------------------------------------------------------------------------
# Load message history
# ---------------------------------------------------------------------------


def load_messages(
    last_evaluated_key: str | None,
) -> LoadMessagesResponse:
    """Return a page of messages from the ``ufscheduler-chat`` table."""
    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("id").eq("General"),
        "ScanIndexForward": False,
        "Limit": MESSAGES_BATCH_SIZE,
    }

    if last_evaluated_key:
        try:
            query_kwargs["ExclusiveStartKey"] = json.loads(last_evaluated_key)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid last_evaluated_key",
            )

    try:
        response = chat_table.query(**query_kwargs)
    except Exception:
        logger.exception("Unable to load messages")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error loading messages",
        )

    items = sorted(response.get("Items", []), key=lambda m: m["timestamp"])
    messages = [ChatMessage(**m) for m in items]

    return LoadMessagesResponse(
        messages=messages,
        last_evaluated_key=response.get("LastEvaluatedKey"),
    )
