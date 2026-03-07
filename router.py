"""
FastAPI route definitions.

Each handler is a thin wrapper that delegates to ``controller``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from utils.auth import get_current_user
from controller import handle_delete_session, stream_chat
from models import ChatDeleteResponse, ChatRequest, HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}


@router.post("/chat")
async def chat(
    body: ChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    return EventSourceResponse(stream_chat(body, user))


@router.delete("/chat/{session_id}", response_model=ChatDeleteResponse)
async def delete_chat(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
):
    return handle_delete_session(session_id, user)
