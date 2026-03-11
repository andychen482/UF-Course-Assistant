"""Chat room request / response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SendMessageRequest(BaseModel):
    message: str


class ChatMessage(BaseModel):
    id: str
    message: str
    user: str
    timestamp: str


class LoadMessagesResponse(BaseModel):
    messages: list[ChatMessage]
    last_evaluated_key: dict[str, Any] | None = None
