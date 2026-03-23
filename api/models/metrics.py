"""Analytics metrics request / response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TrackMajorRequest(BaseModel):
    major: str = Field(..., min_length=1, max_length=200)


class TrackCourseRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=200)


class TrackSearchRequest(BaseModel):
    search_term: str = Field(..., min_length=1, max_length=200)


class MetricsResponse(BaseModel):
    message: str
