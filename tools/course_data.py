"""
Course data loading and indexing module.

Loads the most recent *_clean.json from the courses/ directory into memory
and builds indexes for fast course lookup by code and name.
"""

import json
import os
import glob
from collections import defaultdict


# Resolve paths relative to this file's parent (project root)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COURSES_DIR = os.path.join(_PROJECT_ROOT, "courses")


def _find_latest_clean_json() -> str:
    """Find the most recently modified *_clean.json file in courses/."""
    pattern = os.path.join(_COURSES_DIR, "*_clean.json")
    files = glob.glob(pattern)
    if not files:
        raise FileNotFoundError(
            f"No *_clean.json files found in {_COURSES_DIR}. "
            "Run the scraper first to generate course data."
        )
    # Return the most recently modified file
    return max(files, key=os.path.getmtime)


def _load_courses(filepath: str) -> list[dict]:
    """Load and return the course list from a JSON file."""
    with open(filepath, "r") as f:
        data = json.load(f)
    return data


def _build_indexes(
    courses: list[dict],
) -> tuple[dict[str, list[dict]], list[tuple[str, str, int]]]:
    """
    Build lookup structures from the course list.

    Some course codes have multiple entries (e.g. "Special Topics" with
    different subtitles), so code_index maps each code to a *list* of
    course dicts.

    Returns:
        code_index: dict mapping uppercase course code -> list of course dicts
        name_index: list of (uppercase_code, lowercase_name, entry_idx)
                    for substring search (entry_idx is the position within
                    that code's list so we can return specific entries)
    """
    code_index: dict[str, list[dict]] = defaultdict(list)
    name_index: list[tuple[str, str, int]] = []

    for course in courses:
        code_upper = course["code"].upper()
        idx = len(code_index[code_upper])
        code_index[code_upper].append(course)
        name_index.append((code_upper, course["name"].lower(), idx))

    return dict(code_index), name_index


# ---------------------------------------------------------------------------
# Module-level initialization: load data and build indexes on first import
# ---------------------------------------------------------------------------
_data_file = _find_latest_clean_json()
_courses = _load_courses(_data_file)
_code_index, _name_index = _build_indexes(_courses)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_courses(code: str) -> list[dict]:
    """
    Look up all course entries for a given code (case-insensitive).

    Returns a list of course dicts (may contain multiple entries for
    courses like "Special Topics" that share a code but have different
    subtitles). Returns an empty list if the code is not found.
    """
    return _code_index.get(code.strip().upper(), [])


def search_by_code(query: str, limit: int = 10) -> list[dict]:
    """
    Search courses by code. Tries exact match first, then prefix match.

    Args:
        query: Full or partial course code (e.g. "COP3530" or "COP").
        limit: Maximum number of results to return.

    Returns:
        List of matching course dicts, up to `limit`.
    """
    query_upper = query.strip().upper()

    # Exact match -- return all entries for that code
    exact = _code_index.get(query_upper)
    if exact:
        return exact[:limit]

    # Prefix match -- collect entries from all matching codes
    matches: list[dict] = []
    seen_codes: set[str] = set()
    for code, _, _ in _name_index:
        if code.startswith(query_upper) and code not in seen_codes:
            seen_codes.add(code)
            matches.extend(_code_index[code])
            if len(matches) >= limit:
                break
    return matches[:limit]


def search_by_name(query: str, limit: int = 10) -> list[dict]:
    """
    Search courses by name using case-insensitive substring matching.

    Args:
        query: Search string to match against course names.
        limit: Maximum number of results to return.

    Returns:
        List of matching course dicts, up to `limit`.
    """
    query_lower = query.strip().lower()
    if not query_lower:
        return []

    matches: list[dict] = []
    for code, name, idx in _name_index:
        if query_lower in name:
            matches.append(_code_index[code][idx])
            if len(matches) >= limit:
                break
    return matches[:limit]
