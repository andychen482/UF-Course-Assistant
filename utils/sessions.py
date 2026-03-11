"""
In-memory conversation session store.

Each session is a list of messages bound to an authenticated user's ``sub``
claim.  Sessions expire after a configurable TTL and are lazily pruned.

NOTE: This store lives in process memory, so it only works for
single-container deployments.  For multi-container ECS services, replace this
with a Redis- or DynamoDB-backed implementation that exposes the same API.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from utils.constants import SESSION_TTL_SECONDS


@dataclass
class _Session:
    user_sub: str
    messages: list[dict[str, str]] = field(default_factory=list)
    last_active: float = field(default_factory=time.monotonic)


_store: dict[str, _Session] = {}


def _prune_expired() -> None:
    cutoff = time.monotonic() - SESSION_TTL_SECONDS
    expired = [sid for sid, s in _store.items() if s.last_active < cutoff]
    for sid in expired:
        del _store[sid]


def create_session(user_sub: str) -> str:
    """Create a new session for *user_sub* and return the session id."""
    _prune_expired()
    session_id = uuid.uuid4().hex
    _store[session_id] = _Session(user_sub=user_sub)
    return session_id


def get_session(session_id: str, user_sub: str) -> list[dict[str, str]] | None:
    """Return the message list for *session_id* if it belongs to *user_sub*.

    Returns ``None`` when the session does not exist, is expired, or belongs to
    a different user.
    """
    _prune_expired()
    session = _store.get(session_id)
    if session is None or session.user_sub != user_sub:
        return None
    session.last_active = time.monotonic()
    return session.messages


def add_message(session_id: str, role: str, content: str) -> None:
    """Append a message to the session's conversation history."""
    session = _store.get(session_id)
    if session is not None:
        session.messages.append({"role": role, "content": content})
        session.last_active = time.monotonic()


def delete_session(session_id: str, user_sub: str) -> bool:
    """Delete a session.  Returns ``True`` if it existed and belonged to *user_sub*."""
    session = _store.get(session_id)
    if session is None or session.user_sub != user_sub:
        return False
    del _store[session_id]
    return True
