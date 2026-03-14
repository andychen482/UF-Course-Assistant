"""Chat room route definitions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sse_starlette.sse import EventSourceResponse

from api.controllers.chat_room import event_stream, load_messages, send_message
from api.models.chat_room import ChatMessage, LoadMessagesResponse, SendMessageRequest
from utils.auth import get_current_user

router = APIRouter(prefix="/chat-room", tags=["chat-room"])


@router.get("/messages/stream")
async def events(user: dict[str, Any] = Depends(get_current_user)):
    return EventSourceResponse(event_stream(user))


@router.post("/messages", response_model=ChatMessage)
async def post_message(
    body: SendMessageRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    return await send_message(body.message, user)


@router.get("/messages", response_model=LoadMessagesResponse)
async def get_messages(
    last_evaluated_key: str | None = Query(None),
):
    return load_messages(last_evaluated_key)
