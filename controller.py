"""
Business logic for the chat API.

Keeps route handlers thin by encapsulating session management and agent
streaming here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException, status

from models import ChatRequest
from sessions import add_message, create_session, delete_session, get_session

logger = logging.getLogger("uvicorn.error")

# The running agent instance is injected by the lifespan handler in api.py.
agent = None


def _resolve_session(
    body: ChatRequest, user_sub: str
) -> tuple[str, list[dict[str, str]]]:
    """Return ``(session_id, messages)`` -- creating a new session when needed."""
    session_id = body.session_id

    if session_id:
        messages = get_session(session_id, user_sub)
        if messages is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or expired",
            )
    else:
        session_id = create_session(user_sub)
        messages = get_session(session_id, user_sub)

    return session_id, messages


async def stream_chat(
    body: ChatRequest, user: dict[str, Any]
) -> AsyncGenerator[dict[str, str], None]:
    """Yield SSE event dicts for a single chat turn."""
    user_sub: str = user["sub"]
    session_id, messages = _resolve_session(body, user_sub)

    add_message(session_id, "user", body.prompt)

    accumulated = ""
    try:
        async for event in agent.astream_events(
            {"messages": messages},
            version="v2",
        ):
            kind = event.get("event")
            if kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    token = chunk.content
                    accumulated += token
                    yield {
                        "event": "token",
                        "data": json.dumps(
                            {"token": token, "session_id": session_id}
                        ),
                    }
    except Exception:
        logger.exception("Error during agent streaming")
        yield {
            "event": "error",
            "data": json.dumps({"detail": "Internal error during generation"}),
        }
        return

    add_message(session_id, "assistant", accumulated)
    yield {
        "event": "done",
        "data": json.dumps({"session_id": session_id}),
    }


def handle_delete_session(
    session_id: str, user: dict[str, Any]
) -> dict[str, str]:
    """Delete a session and return a confirmation payload."""
    deleted = delete_session(session_id, user["sub"])
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return {"status": "deleted", "session_id": session_id}
