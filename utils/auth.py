"""
Cognito JWT validation for FastAPI.

Uses PyJWT's PyJWKClient to fetch, cache, and look up the correct signing key
from the Cognito User Pool's JWKS endpoint, then validates the Bearer token on
every request.
"""

from __future__ import annotations

from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError

from constants import (
    COGNITO_APP_CLIENT_ID,
    COGNITO_REGION,
    COGNITO_USER_POOL_ID,
    JWKS_TTL_SECONDS,
)

_bearer_scheme = HTTPBearer()


def _issuer_url() -> str:
    return (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
        f"{COGNITO_USER_POOL_ID}"
    )


def _jwks_url() -> str:
    return f"{_issuer_url()}/.well-known/jwks.json"


# PyJWKClient handles fetching, caching (lifespan), key lookup by kid,
# and automatic retry on key rotation.
_jwks_client = PyJWKClient(_jwks_url(), lifespan=JWKS_TTL_SECONDS)


def _decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a Cognito JWT (id_token or access_token)."""
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
    except PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unable to find matching signing key: {exc}",
        )

    try:
        claims: dict[str, Any] = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=COGNITO_APP_CLIENT_ID,
            issuer=_issuer_url(),
        )
    except PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {exc}",
        )

    if claims.get("token_use") not in ("id", "access"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token_use claim",
        )

    return claims


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency -- returns decoded JWT claims or raises 401."""
    return _decode_token(credentials.credentials)
