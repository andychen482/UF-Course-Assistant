"""Chat request / response models."""

from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    prompt: str
    session_id: str | None = None
    term: str | None = None
    year: str | None = None
    selected_courses: list[str] | None = None


class ChatDeleteResponse(BaseModel):
    status: str
    session_id: str
