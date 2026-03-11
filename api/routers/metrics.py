"""Analytics metrics route definitions."""

from __future__ import annotations

from fastapi import APIRouter

from api.controllers.metrics import track_course, track_major, track_search
from api.models.metrics import (
    MetricsResponse,
    TrackCourseRequest,
    TrackMajorRequest,
    TrackSearchRequest,
)

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.post("/major", response_model=MetricsResponse)
async def major(body: TrackMajorRequest):
    return track_major(body.major)


@router.post("/course", response_model=MetricsResponse)
async def course(body: TrackCourseRequest):
    return track_course(body.code, body.name)


@router.post("/search", response_model=MetricsResponse)
async def search(body: TrackSearchRequest):
    return track_search(body.search_term)
