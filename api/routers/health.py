"""Health-check route."""

from __future__ import annotations

from fastapi import APIRouter

from api.models.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}
