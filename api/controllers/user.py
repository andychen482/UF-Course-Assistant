"""
User management controller.

Handles username registration and profile retrieval.  Identity is derived
entirely from the Cognito JWT -- the client never supplies its own user ID.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Key
from fastapi import HTTPException, status

from api.models.user import SetUsernameResponse, UserProfile
from utils.dynamo import users_table

logger = logging.getLogger("uvicorn.error")


def set_username(username: str, user: dict[str, Any]) -> SetUsernameResponse:
    """Register a username for the authenticated user.

    The user's ``sub``, ``email``, and ``name`` are taken from the JWT claims
    so the client cannot forge them.
    """
    if " " in username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username should not contain spaces",
        )

    try:
        result = users_table.query(
            IndexName="username-index",
            KeyConditionExpression=Key("username").eq(username),
        )
        if result.get("Items"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username is already taken",
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unable to check username uniqueness")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error checking username",
        )

    item: dict[str, Any] = {
        "sub": user["sub"],
        "username": username,
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "profilePic": user.get("picture", ""),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    try:
        users_table.put_item(Item=item)
    except Exception:
        logger.exception("Unable to set username")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error setting username",
        )

    return SetUsernameResponse(message="Username set successfully")


def get_profile(user: dict[str, Any]) -> UserProfile:
    """Return the profile for the authenticated user."""
    try:
        result = users_table.get_item(Key={"sub": user["sub"]})
    except Exception:
        logger.exception("Unable to get user profile")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error getting user profile",
        )

    item = result.get("Item")
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    return UserProfile(
        sub=item.get("sub", ""),
        username=item.get("username", ""),
        email=item.get("email"),
        name=item.get("name"),
        profile_pic=item.get("profilePic"),
        created_at=item.get("createdAt"),
    )
