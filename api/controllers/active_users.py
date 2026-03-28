"""
Active-users presence controller.

Tracks the number of concurrently connected visitors (authenticated or not)
via an unauthenticated SSE stream.  Every browser tab that opens the stream
increments the counter; disconnecting decrements it.

NOTE: The subscriber set lives in process memory.  For multi-container ECS
deployments, swap the broadcast mechanism for Redis pub/sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from utils.dynamo import metrics_table

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


async def _broadcast_active_users() -> None:
    event = {
        "event": "active_users",
        "data": json.dumps({"active_users": _active_users}),
    }
    for queue in _subscribers:
        await queue.put(event)


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
# SSE event stream (no authentication required)
# ---------------------------------------------------------------------------


async def active_users_stream() -> AsyncGenerator[dict[str, str], None]:
    """SSE generator: yields ``active_users`` events to all visitors."""
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
