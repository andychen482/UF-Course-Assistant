"""Active-users route definitions (unauthenticated)."""

from __future__ import annotations

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from api.controllers.active_users import active_users_stream

router = APIRouter(prefix="/active-users", tags=["active-users"])


@router.get("/stream")
async def events():
    return EventSourceResponse(active_users_stream())
