"""Pydantic request / response models for the API."""

from __future__ import annotations

from api.models.ai_chat import ChatDeleteResponse, ChatRequest
from api.models.chat_room import ChatMessage, LoadMessagesResponse, SendMessageRequest
from api.models.health import HealthResponse
from api.models.metrics import (
    MetricsResponse,
    TrackCourseRequest,
    TrackMajorRequest,
    TrackSearchRequest,
)
from api.models.user import SetUsernameRequest, SetUsernameResponse, UserProfile

__all__ = [
    "ChatDeleteResponse",
    "ChatMessage",
    "ChatRequest",
    "HealthResponse",
    "LoadMessagesResponse",
    "MetricsResponse",
    "SendMessageRequest",
    "SetUsernameRequest",
    "SetUsernameResponse",
    "TrackCourseRequest",
    "TrackMajorRequest",
    "TrackSearchRequest",
    "UserProfile",
]
