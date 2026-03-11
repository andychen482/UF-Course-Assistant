"""Analytics metrics request / response models."""

from __future__ import annotations

from pydantic import BaseModel


class TrackMajorRequest(BaseModel):
    major: str


class TrackCourseRequest(BaseModel):
    code: str
    name: str


class TrackSearchRequest(BaseModel):
    search_term: str


class MetricsResponse(BaseModel):
    message: str
