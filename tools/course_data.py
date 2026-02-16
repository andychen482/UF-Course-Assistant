"""
Course data module -- live queries against the UF One.UF Schedule of Courses API.

All searches hit the API in real time so results are always up to date.
"""

import logging
from datetime import date

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API configuration
# ---------------------------------------------------------------------------

_BASE_URL = "https://one.ufl.edu/apix/soc/schedule/"
_CATEGORY = "RES"
_TIMEOUT = 15  # seconds


def _current_term() -> str:
    """Auto-detect the current UF term code based on today's date.

    Term format: ``2`` + 2-digit year + semester digit
        Spring = 1, Summer = 5, Fall = 8
    Example: Spring 2026 -> ``2261``
    """
    today = date.today()
    year = today.year % 100
    month = today.month
    if month <= 4:
        sem = "1"  # Spring
    elif month <= 7:
        sem = "5"  # Summer
    else:
        sem = "8"  # Fall
    return f"2{year}{sem}"


# Resolved once at import time; override with set_term() if needed.
_term = _current_term()


def set_term(term: str) -> None:
    """Override the auto-detected term (e.g. ``"2261"`` for Spring 2026)."""
    global _term
    _term = term


def get_term() -> str:
    """Return the currently configured term code."""
    return _term


# ---------------------------------------------------------------------------
# Internal API helpers
# ---------------------------------------------------------------------------

def _query_api(extra_params: dict) -> list[dict]:
    """Make a single request to the UF SOC API and return a list of courses.

    The API returns at most 50 courses per call.  For targeted searches
    (by code or title) this is almost always sufficient.
    """
    params = {
        "category": _CATEGORY,
        "term": _term,
        "last-control-number": 0,
    }
    params.update(extra_params)

    try:
        resp = requests.get(_BASE_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("UF API request failed: %s", exc)
        return []
    except ValueError:
        logger.error("UF API returned invalid JSON")
        return []

    courses: list[dict] = []
    if isinstance(data, list):
        for item in data:
            for course in item.get("COURSES", []):
                # Add codeWithSpace for display convenience
                code = course.get("code", "")
                course["codeWithSpace"] = code[:3] + " " + code[3:] if len(code) > 3 else code
                courses.append(course)

    return courses


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_by_code(query: str, limit: int = 10) -> list[dict]:
    """Search courses by course code (exact or prefix).

    Args:
        query: Full or partial course code (e.g. ``"COP3530"`` or ``"COP"``).
        limit: Maximum number of results to return.

    Returns:
        List of course dicts from the API.
    """
    courses = _query_api({"course-code": query.strip()})
    return courses[:limit]


def search_by_name(query: str, limit: int = 10) -> list[dict]:
    """Search courses by name / title substring.

    Args:
        query: Search string to match against course titles.
        limit: Maximum number of results to return.

    Returns:
        List of course dicts from the API.
    """
    courses = _query_api({"course-title": query.strip()})
    return courses[:limit]


def get_courses(code: str) -> list[dict]:
    """Look up all course entries for a given exact code.

    Returns a list because some codes (e.g. Special Topics) have multiple
    listings with different subtitles.
    """
    return _query_api({"course-code": code.strip()})
