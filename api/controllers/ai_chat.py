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

from api.models.ai_chat import (
    ChatDeleteResponse,
    ChatDetailResponse,
    ChatHistoryResponse,
    ChatRequest,
    ChatSummary,
)
from tools.course_data import set_request_term
from tools.scheduler_actions import CLIENT_ACTION_TOOLS
from utils.sessions import (
    add_message,
    create_session,
    delete_session,
    get_session,
    get_session_detail,
    list_sessions,
)

logger = logging.getLogger("uvicorn.error")

_TERM_TO_SEM = {"spring": "1", "summer": "5", "fall": "8"}


def _to_term_code(term: str, year: str) -> str:
    """Convert frontend term/year (e.g. 'summer', '26') to UF code (e.g. '2265')."""
    sem = _TERM_TO_SEM.get(term.lower(), "1")
    return f"2{year}{sem}"


# The running agent instance is injected by the lifespan handler in main.py
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
        return session_id, messages

    return create_session(user_sub)


async def stream_chat(
    body: ChatRequest, user: dict[str, Any]
) -> AsyncGenerator[dict[str, str], None]:
    """Yield SSE event dicts for a single chat turn."""
    user_sub: str = user["sub"]
    session_id, messages = _resolve_session(body, user_sub)

    add_message(session_id, user_sub, "user", body.prompt)
    messages.append({"role": "user", "content": body.prompt})

    if body.term and body.year:
        term_code = _to_term_code(body.term, body.year)
        set_request_term(term_code)
    else:
        logger.info("No term/year from frontend, using auto-detected default")

    term_label = (
        f"{body.term.capitalize()} 20{body.year}"
        if body.term and body.year
        else "current semester"
    )
    courses_str = ", ".join(body.selected_courses) if body.selected_courses else "None"
    context_msg = {
        "role": "system",
        "content": (
            f"[STUDENT CONTEXT] Viewing term: {term_label}. "
            f"Currently selected courses: {courses_str}."
        ),
    }
    messages_for_agent = [context_msg] + list(messages)

    accumulated = ""
    try:
        async for event in agent.astream_events(
            {"messages": messages_for_agent},
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
            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                tool_input = event["data"].get("input", {})
                logger.info("Tool call: %s | params: %s", tool_name, tool_input)
                if tool_name in CLIENT_ACTION_TOOLS:
                    yield {
                        "event": "tool_call",
                        "data": json.dumps({
                            "tool_call_id": event.get("run_id", ""),
                            "name": tool_name,
                            "arguments": event["data"].get("input", {}),
                            "session_id": session_id,
                        }),
                    }
    except Exception:
        logger.exception("Error during agent streaming")
        yield {
            "event": "error",
            "data": json.dumps({"detail": "Internal error during generation"}),
        }
        return

    add_message(session_id, user_sub, "assistant", accumulated)
    yield {
        "event": "done",
        "data": json.dumps({"session_id": session_id}),
    }


def handle_delete_session(
    session_id: str, user: dict[str, Any]
) -> ChatDeleteResponse:
    """Delete a session and return a confirmation payload."""
    deleted = delete_session(session_id, user["sub"])
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return ChatDeleteResponse(status="deleted", session_id=session_id)


def get_chat_history(user: dict[str, Any]) -> ChatHistoryResponse:
    """Return a summary list of all conversations for the authenticated user."""
    items = list_sessions(user["sub"])
    chats = [
        ChatSummary(
            session_id=item["session_id"],
            title=item.get("title", "New Chat"),
            updated_at=item.get("updated_at", ""),
        )
        for item in items
    ]
    return ChatHistoryResponse(chats=chats)


def get_chat_detail_handler(
    session_id: str, user: dict[str, Any]
) -> ChatDetailResponse:
    """Return the full conversation for a specific session."""
    item = get_session_detail(session_id, user["sub"])
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return ChatDetailResponse(
        session_id=item["session_id"],
        title=item.get("title", "New Chat"),
        messages=item.get("messages", []),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
    )
