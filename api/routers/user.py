"""User route definitions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.controllers.user import get_profile, set_username
from api.models.user import SetUsernameRequest, SetUsernameResponse, UserProfile
from utils.auth import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/username", response_model=SetUsernameResponse)
async def post_username(
    body: SetUsernameRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    return set_username(body.username, user)


@router.get("/me", response_model=UserProfile)
async def get_me(
    user: dict[str, Any] = Depends(get_current_user),
):
    return get_profile(user)
