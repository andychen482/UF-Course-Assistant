"""
Pydantic request / response models for the API.
"""

from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    prompt: str
    session_id: str | None = None


class ChatDeleteResponse(BaseModel):
    status: str
    session_id: str


class HealthResponse(BaseModel):
    status: str
