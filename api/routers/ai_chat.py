"""Chat route definitions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from api.controllers.ai_chat import (
    get_chat_detail_handler,
    get_chat_history,
    handle_delete_session,
    stream_chat,
)
from api.models.ai_chat import (
    ChatDeleteResponse,
    ChatDetailResponse,
    ChatHistoryResponse,
    ChatRequest,
)
from utils.auth import get_current_user

router = APIRouter(prefix="/ai-chat", tags=["ai-chat"])


@router.post("")
async def ai_chat(
    body: ChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    return EventSourceResponse(stream_chat(body, user))


@router.get("/history", response_model=ChatHistoryResponse)
async def chat_history(
    user: dict[str, Any] = Depends(get_current_user),
):
    return get_chat_history(user)


@router.get("/{session_id}", response_model=ChatDetailResponse)
async def chat_detail(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
):
    return get_chat_detail_handler(session_id, user)


@router.delete("/{session_id}", response_model=ChatDeleteResponse)
async def delete_chat(
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
):
    return handle_delete_session(session_id, user)
