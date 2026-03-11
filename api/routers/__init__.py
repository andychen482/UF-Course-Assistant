"""Aggregate all sub-routers into a single top-level router."""

from __future__ import annotations

from fastapi import APIRouter

from api.routers.ai_chat import router as ai_chat_router
from api.routers.chat_room import router as chat_room_router
from api.routers.health import router as health_router
from api.routers.metrics import router as metrics_router
from api.routers.user import router as user_router

router = APIRouter()

router.include_router(health_router)
router.include_router(ai_chat_router)
router.include_router(chat_room_router)
router.include_router(metrics_router)
router.include_router(user_router)
