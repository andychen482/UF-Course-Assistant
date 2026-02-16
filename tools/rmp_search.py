"""
LangChain tool for looking up professor ratings on RateMyProfessors.

Queries the RMP GraphQL API on the fly for University of Florida professors.
"""

import requests
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# RMP GraphQL config
# ---------------------------------------------------------------------------

_RMP_URL = "https://www.ratemyprofessors.com/graphql"
_UF_SCHOOL_ID = "U2Nob29sLTExMDA="  # Base64 for "School-1100" (UF)

_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Authorization": "Basic dGVzdDp0ZXN0",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Host": "www.ratemyprofessors.com",
    "Origin": "https://www.ratemyprofessors.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_RATINGS_QUERY = """
query TeacherRatingsPageQuery($id: ID!, $count: Int!) {
    node(id: $id) {
        ... on Teacher {
            ratings(first: $count) {
                edges {
                    node {
                        comment
                        class
                        date
                        qualityRating
                        difficultyRatingRounded
                        grade
                        isForOnlineClass
                        ratingTags
                        iWouldTakeAgain
                        thumbsUpTotal
                        thumbsDownTotal
                    }
                }
            }
        }
    }
}
"""

_SEARCH_QUERY = """
query NewSearchTeachersQuery($query: TeacherSearchQuery!) {
    newSearch {
        teachers(query: $query) {
            didFallback
            edges {
                cursor
                node {
                    id
                    legacyId
                    firstName
                    lastName
                    avgRatingRounded
                    numRatings
                    wouldTakeAgainPercentRounded
                    wouldTakeAgainCount
                    teacherRatingTags {
                        id
                        legacyId
                        tagCount
                        tagName
                    }
                    mostUsefulRating {
                        id
                        class
                        isForOnlineClass
                        legacyId
                        comment
                        helpfulRatingRounded
                        ratingTags
                        grade
                        date
                        iWouldTakeAgain
                        qualityRating
                        difficultyRatingRounded
                        teacherNote {
                            id
                            comment
                            createdAt
                            class
                        }
                        thumbsDownTotal
                        thumbsUpTotal
                    }
                    avgDifficultyRounded
                    school {
                        name
                        id
                    }
                    department
                }
            }
        }
    }
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_professor(name: str) -> list[dict]:
    """
    Query RMP for a professor by name at UF. Returns a list of matching
    professor node dicts (may be empty).
    """
    payload = {
        "query": _SEARCH_QUERY,
        "variables": {
            "query": {
                "text": name,
                "schoolID": _UF_SCHOOL_ID,
            }
        },
    }

    try:
        resp = requests.post(_RMP_URL, json=payload, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        return [{"_error": f"Request failed: {e}"}]

    try:
        data = resp.json()
    except ValueError:
        return [{"_error": "Failed to decode RMP response"}]

    edges = (
        data.get("data", {})
        .get("newSearch", {})
        .get("teachers", {})
        .get("edges", [])
    )

    return [edge["node"] for edge in edges]


def _fetch_ratings(professor_id: str, count: int = 10) -> list[dict]:
    """Fetch the most recent ratings for a professor by their RMP node ID."""
    payload = {
        "query": _RATINGS_QUERY,
        "variables": {"id": professor_id, "count": count},
    }

    try:
        resp = requests.post(_RMP_URL, json=payload, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    edges = (
        data.get("data", {})
        .get("node", {})
        .get("ratings", {})
        .get("edges", [])
    )

    return [edge["node"] for edge in edges]


def _resolve_professor(name: str) -> dict | None:
    """Search for a professor and return the best-matching node with ratings.

    Returns the full node dict (including ``id`` for follow-up queries), or
    None if no match is found.
    """
    nodes = _fetch_professor(name)
    if not nodes or "_error" in nodes[0]:
        return None

    name_lower = name.lower()
    for node in nodes:
        full = f"{node.get('firstName', '')} {node.get('lastName', '')}".strip().lower()
        if node.get("numRatings", 0) > 0 and full == name_lower:
            return node

    # Fallback: first node with ratings
    for node in nodes:
        if node.get("numRatings", 0) > 0:
            return node

    return None


def _format_review(review: dict, index: int) -> str:
    """Format a single rating/review into readable text."""
    r_class = review.get("class", "N/A")
    r_quality = review.get("qualityRating", "")
    r_diff = review.get("difficultyRatingRounded", "")
    r_grade = review.get("grade", "")
    r_date = review.get("date", "")
    r_online = review.get("isForOnlineClass")
    r_would = review.get("iWouldTakeAgain")
    comment = (review.get("comment") or "").strip()
    tags = review.get("ratingTags", "")
    thumbs_up = review.get("thumbsUpTotal", 0)
    thumbs_down = review.get("thumbsDownTotal", 0)

    # Trim the UTC timezone suffix for cleaner display
    if r_date and " +0000 UTC" in r_date:
        r_date = r_date.replace(" +0000 UTC", "")

    meta = []
    if r_class:
        meta.append(f"Course: {r_class}")
    if r_quality:
        meta.append(f"Quality: {r_quality}/5")
    if r_diff:
        meta.append(f"Difficulty: {r_diff}/5")
    if r_grade:
        meta.append(f"Grade: {r_grade}")
    if r_online:
        meta.append("Online")
    if r_would is True:
        meta.append("Would take again")
    elif r_would is False:
        meta.append("Would NOT take again")

    lines = [f"  {index}. {' | '.join(meta)}"]
    if r_date:
        lines.append(f"     Date: {r_date}")
    if tags:
        lines.append(f"     Tags: {tags}")
    if comment:
        if len(comment) > 500:
            comment = comment[:497] + "..."
        lines.append(f"     \"{comment}\"")
    if thumbs_up or thumbs_down:
        lines.append(f"     Helpful: {thumbs_up} up / {thumbs_down} down")

    return "\n".join(lines)


def _format_professor(node: dict) -> str:
    """Format a professor node into readable text for the LLM."""
    first = node.get("firstName", "")
    last = node.get("lastName", "")
    full_name = f"{first} {last}".strip()
    dept = node.get("department", "Unknown")
    school = node.get("school", {}).get("name", "Unknown")
    legacy_id = node.get("legacyId", "")

    avg_rating = node.get("avgRatingRounded", "N/A")
    avg_diff = node.get("avgDifficultyRounded", "N/A")
    num_ratings = node.get("numRatings", 0)
    would_take_again = node.get("wouldTakeAgainPercentRounded", -1)

    # Clean up floating-point display (e.g. 4.800000000000001 -> 4.8)
    if isinstance(avg_rating, float):
        avg_rating = round(avg_rating, 1)
    if isinstance(avg_diff, float):
        avg_diff = round(avg_diff, 1)

    lines = [
        f"{full_name}",
        f"  Department: {dept} | School: {school}",
        f"  Overall Rating: {avg_rating}/5 ({num_ratings} ratings)",
        f"  Difficulty: {avg_diff}/5",
    ]

    if would_take_again >= 0:
        lines.append(f"  Would Take Again: {would_take_again}%")

    # Top rating tags
    tags = node.get("teacherRatingTags", [])
    if tags:
        sorted_tags = sorted(tags, key=lambda t: t.get("tagCount", 0), reverse=True)
        top_tags = [t["tagName"] for t in sorted_tags[:5] if t.get("tagCount", 0) > 0]
        if top_tags:
            lines.append(f"  Top Tags: {', '.join(top_tags)}")

    # Most useful review
    review = node.get("mostUsefulRating")
    if review:
        comment = (review.get("comment") or "").strip()
        r_class = review.get("class", "")
        r_quality = review.get("qualityRating", "")
        r_diff = review.get("difficultyRatingRounded", "")
        r_grade = review.get("grade", "")
        r_date = review.get("date", "")
        r_online = review.get("isForOnlineClass")

        lines.append("  ---")
        lines.append("  Most Helpful Review:")
        review_meta = []
        if r_class:
            review_meta.append(f"Course: {r_class}")
        if r_quality:
            review_meta.append(f"Quality: {r_quality}/5")
        if r_diff:
            review_meta.append(f"Difficulty: {r_diff}/5")
        if r_grade:
            review_meta.append(f"Grade: {r_grade}")
        if r_online:
            review_meta.append("(Online)")
        if review_meta:
            lines.append(f"    {' | '.join(review_meta)}")
        if r_date:
            lines.append(f"    Date: {r_date}")
        if comment:
            # Truncate very long comments
            if len(comment) > 400:
                comment = comment[:397] + "..."
            lines.append(f"    \"{comment}\"")

    if legacy_id:
        lines.append(f"  RMP Link: https://www.ratemyprofessors.com/professor/{legacy_id}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LangChain Tool
# ---------------------------------------------------------------------------

@tool
def search_professor_rating(professor_name: str) -> str:
    """Look up a professor's rating on RateMyProfessors for the University of Florida.

    Use this tool when a student asks about a professor's rating, reviews,
    difficulty, or reputation. Provide the professor's full name as it appears
    in the course catalog (e.g. "Amanpreet Kapoor").

    Args:
        professor_name: The professor's full name (e.g. "Amanpreet Kapoor").
    """
    name = professor_name.strip()
    if not name:
        return "Please provide a professor name to search."

    nodes = _fetch_professor(name)

    # Handle request errors
    if nodes and "_error" in nodes[0]:
        return f"Could not reach RateMyProfessors: {nodes[0]['_error']}"

    if not nodes:
        return (
            f'No results found for "{professor_name}" on RateMyProfessors at UF. '
            "The professor may not have a profile, or try a different name spelling."
        )

    # Filter to UF professors with ratings, and try exact name match first
    name_lower = name.lower()
    uf_matches = []
    for node in nodes:
        full = f"{node.get('firstName', '')} {node.get('lastName', '')}".strip().lower()
        num_ratings = node.get("numRatings", 0)
        if num_ratings > 0 and full == name_lower:
            uf_matches.append(node)

    # If no exact match, show all UF results that have ratings
    if not uf_matches:
        uf_matches = [n for n in nodes if n.get("numRatings", 0) > 0]

    if not uf_matches:
        return (
            f'Found a profile for "{professor_name}" on RateMyProfessors but '
            "they have no ratings yet."
        )

    formatted = [_format_professor(m) for m in uf_matches[:3]]

    if len(uf_matches) == 1:
        return formatted[0]

    header = f'Found {len(uf_matches)} matching professor(s) for "{professor_name}":\n'
    separator = "\n" + "-" * 50 + "\n"
    return header + separator.join(formatted)


@tool
def get_professor_reviews(professor_name: str, num_reviews: int = 5) -> str:
    """Get the most recent student reviews for a UF professor from RateMyProfessors.

    Use this tool when a student wants to see recent reviews, comments, or
    detailed feedback about a professor beyond just the overall rating.
    Returns the N most recent individual reviews with quality/difficulty
    scores, grades, dates, tags, and student comments.

    Args:
        professor_name: The professor's full name (e.g. "Amanpreet Kapoor").
        num_reviews: Number of recent reviews to fetch (default 5, max 20).
    """
    name = professor_name.strip()
    if not name:
        return "Please provide a professor name to search."

    num_reviews = max(1, min(num_reviews, 20))

    node = _resolve_professor(name)
    if node is None:
        return (
            f'No professor found matching "{professor_name}" on '
            "RateMyProfessors at UF. Try a different name spelling."
        )

    first = node.get("firstName", "")
    last = node.get("lastName", "")
    full_name = f"{first} {last}".strip()
    prof_id = node["id"]
    legacy_id = node.get("legacyId", "")
    avg_rating = node.get("avgRatingRounded", "N/A")
    avg_diff = node.get("avgDifficultyRounded", "N/A")
    num_total = node.get("numRatings", 0)

    if isinstance(avg_rating, float):
        avg_rating = round(avg_rating, 1)
    if isinstance(avg_diff, float):
        avg_diff = round(avg_diff, 1)

    reviews = _fetch_ratings(prof_id, count=num_reviews)

    if not reviews:
        return (
            f'{full_name} has a profile on RateMyProfessors '
            f"({avg_rating}/5, {num_total} ratings) but no individual "
            "reviews could be retrieved."
        )

    lines = [
        f"{full_name} -- {num_total} total ratings",
        f"Overall: {avg_rating}/5 | Difficulty: {avg_diff}/5",
        f"Showing {len(reviews)} most recent review(s):",
        "",
    ]

    for i, review in enumerate(reviews, 1):
        lines.append(_format_review(review, i))
        lines.append("")

    if legacy_id:
        lines.append(
            f"See all reviews: https://www.ratemyprofessors.com/professor/{legacy_id}"
        )

    return "\n".join(lines).rstrip()
