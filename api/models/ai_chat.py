"""Chat request / response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    term: str | None = Field(default=None, max_length=10)
    year: str | None = Field(default=None, max_length=4)
    selected_courses: list[str] | None = Field(default=None, max_length=50)

    class Config:
        str_max_length = 2000


class ChatDeleteResponse(BaseModel):
    status: str
    session_id: str
