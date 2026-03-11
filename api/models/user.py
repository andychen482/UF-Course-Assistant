"""User request / response models."""

from __future__ import annotations

from pydantic import BaseModel


class SetUsernameRequest(BaseModel):
    username: str


class SetUsernameResponse(BaseModel):
    message: str


class UserProfile(BaseModel):
    sub: str
    username: str
    email: str | None = None
    name: str | None = None
    profile_pic: str | None = None
    created_at: str | None = None