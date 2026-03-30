"""
LangChain tool for searching Reddit posts/comments from r/UFL related to UF courses, majors, or topics.

Searches a SQLite database with FTS5 for relevant posts and comments, scores them by
relevance and engagement, filters out vulgar content, and returns rich context
for the LLM to synthesize actionable advice.
"""

import os
import re

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from langchain_core.tools import tool
from tools.course_data import search_by_code, search_by_name
import requests
import time

from scrapers import reddit_db

# Vulgarity filter (expand as needed)
VULGAR_WORDS = [
    "fuck", "shit", "bitch", "asshole", "dick", "piss", "cunt", "bastard", "slut", "whore",
    "fag", "cock", "damn", "crap", "retard", "nigger", "nigga", "twat", "wank", "wanker"
]
VULGAR_PATTERN = re.compile(r"\b(" + "|".join(re.escape(word) for word in VULGAR_WORDS) + r")\b", re.IGNORECASE)

# Regex to extract UF course codes from any text (e.g. COP3530, CEN 3031, mac2311)
_COURSE_CODE_RE = re.compile(r"\b([A-Za-z]{2,4})\s?(\d{4}[A-Za-z]?)\b")

# Common stop words to exclude from tokenized matching
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "about", "between",
    "through", "after", "before", "during", "without", "and", "but", "or",
    "nor", "not", "so", "yet", "both", "either", "neither", "each", "every",
    "all", "any", "few", "more", "most", "other", "some", "such", "no",
    "only", "same", "than", "too", "very", "just", "because", "if", "when",
    "while", "how", "what", "which", "who", "whom", "this", "that", "these",
    "those", "i", "me", "my", "we", "our", "you", "your", "he", "him",
    "his", "she", "her", "it", "its", "they", "them", "their", "up", "out",
    "also", "then", "like", "really", "think", "know", "get", "got",
})

# FTS5 special tokens that must be stripped from user queries
_FTS5_SPECIAL = re.compile(r'[*(){}[\]^~<>]')


def _clean_text(text: str) -> str:
    """Remove vulgar words and excessive whitespace from text."""
    if not text:
        return ""
    text = VULGAR_PATTERN.sub("[redacted]", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> set[str]:
    """Lowercase tokenize text into meaningful words, stripping stop words."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 1}


def _extract_course_codes(text: str) -> list[str]:
    """Extract all UF course codes from text, returning normalized forms.

    E.g. "CEN3031 and COP 4600" -> ["CEN3031", "COP4600"]
    """
    codes = []
    for match in _COURSE_CODE_RE.finditer(text):
        prefix = match.group(1).upper()
        number = match.group(2).upper()
        code = prefix + number
        if code not in codes:
            codes.append(code)
    return codes


def _add_code_variants(code: str, phrases: list[str]) -> None:
    """Append all case/spacing variants of a course code to *phrases*."""
    code_upper = code.upper()
    code_withspace = re.sub(r"([A-Z]+)(\d+)", r"\1 \2", code_upper)
    for variant in [code_upper, code_withspace, code_upper.lower(), code_withspace.lower()]:
        if variant not in phrases:
            phrases.append(variant)


def _expand_query(query: str) -> dict:
    """
    Expand the query into multiple search forms and keywords.

    Handles both single course codes ("COP3530") AND natural language queries
    ("are CEN3031, and COP4600 difficult to take together") by extracting
    individual course codes from the text.

    Returns a dict with:
      - exact_phrases: list of exact substrings to match (course code variants, etc.)
      - keywords: set of meaningful tokens for fuzzy matching
      - course_codes: list of normalized course codes found in the query
    """
    result = {"exact_phrases": [], "keywords": set(), "course_codes": []}

    query_stripped = query.strip()

    # 1. Extract individual course codes from the query
    course_codes = _extract_course_codes(query_stripped)
    result["course_codes"] = course_codes

    # 2. For each course code, add all variants as exact phrases
    for code in course_codes:
        _add_code_variants(code, result["exact_phrases"])

    # 3. If no course codes found, treat the whole query as a phrase
    #    and resolve it as a course name -> course codes
    if not course_codes:
        result["exact_phrases"].append(query_stripped)
        try:
            for course in search_by_name(query_stripped, limit=3):
                code = course.get("code", "")
                if code and code not in result["course_codes"]:
                    result["course_codes"].append(code)
                    _add_code_variants(code, result["exact_phrases"])
        except Exception:
            pass

    # 4. Resolve each course code -> course name so we query both
    for code in course_codes:
        try:
            for course in search_by_code(code, limit=1):
                name = course.get("name", "")
                if name:
                    if name not in result["exact_phrases"]:
                        result["exact_phrases"].append(name)
                    result["keywords"].update(_tokenize(name))
                    break
        except Exception:
            pass

    # 5. Keywords from the full query (minus stop words)
    result["keywords"].update(_tokenize(query_stripped))

    return result


def _score_text_match(text: str, expanded_query: dict) -> float:
    """
    Score how well a text matches the expanded query.

    Returns a relevance score (0.0 = no match, higher = better match).
    Exact phrase matches are weighted much higher than keyword overlap.
    """
    if not text:
        return 0.0
    text_lower = text.lower()
    score = 0.0

    # Exact phrase matches (high value)
    for phrase in expanded_query["exact_phrases"]:
        if phrase.lower() in text_lower:
            score += 10.0

    # Keyword overlap (moderate value)
    if expanded_query["keywords"]:
        text_tokens = _tokenize(text)
        overlap = expanded_query["keywords"] & text_tokens
        if overlap:
            score += len(overlap) / len(expanded_query["keywords"]) * 3.0

    return score


def _build_fts_query(expanded_query: dict) -> str:
    """Build an FTS5 MATCH expression from expanded query phrases and keywords."""
    parts = []
    for phrase in expanded_query["exact_phrases"]:
        safe = _FTS5_SPECIAL.sub('', phrase).replace('"', '""').strip()
        if safe:
            parts.append(f'"{safe}"')
    for keyword in expanded_query["keywords"]:
        safe = _FTS5_SPECIAL.sub('', keyword).replace('"', '""').strip()
        if safe:
            parts.append(safe)
    return " OR ".join(parts) if parts else ""


def _search_reddit(query: str, limit: int = 10) -> list[dict]:
    """
    Search the Reddit SQLite database for posts/comments relevant to the query.

    Uses FTS5 for fast candidate retrieval, then re-ranks with scoring logic
    (exact phrase + keyword overlap + engagement).
    """
    reddit_db.init_db()
    conn = reddit_db.get_connection()

    expanded = _expand_query(query)
    fts_query = _build_fts_query(expanded)
    if not fts_query:
        return []

    # Phase 1: FTS5 candidate retrieval
    candidate_ids = set()

    try:
        # Posts matching in title/selftext
        post_rows = conn.execute("""
            SELECT p.id FROM posts p
            JOIN posts_fts ON posts_fts.rowid = p.rowid
            WHERE posts_fts MATCH ?
            ORDER BY rank
            LIMIT 200
        """, (fts_query,)).fetchall()
        candidate_ids.update(row["id"] for row in post_rows)
    except Exception:
        pass

    try:
        # Posts with matching comments
        comment_rows = conn.execute("""
            SELECT DISTINCT c.post_id FROM comments c
            JOIN comments_fts ON comments_fts.rowid = c.rowid
            WHERE comments_fts MATCH ?
            LIMIT 200
        """, (fts_query,)).fetchall()
        candidate_ids.update(row["post_id"] for row in comment_rows)
    except Exception:
        pass

    if not candidate_ids:
        return []

    # Phase 2: Fetch full data and re-rank in Python
    placeholders = ",".join("?" * len(candidate_ids))
    id_list = list(candidate_ids)
    posts = conn.execute(
        f"SELECT * FROM posts WHERE id IN ({placeholders})", id_list
    ).fetchall()

    scored_results = []
    for post in posts:
        title = post["title"] or ""
        selftext = post["selftext"] or ""
        flair = post["flair"] or ""

        # Score the post itself (title weighted highest)
        relevance = (
            _score_text_match(title, expanded) * 2.0
            + _score_text_match(selftext, expanded)
            + _score_text_match(flair, expanded) * 0.5
        )

        # Fetch and score comments
        comments = conn.execute(
            "SELECT * FROM comments WHERE post_id = ?", (post["id"],)
        ).fetchall()

        scored_comments = []
        for comment in comments:
            body = comment["body"] or ""
            comment_relevance = _score_text_match(body, expanded)
            if comment_relevance > 0:
                relevance += comment_relevance * 0.5
                scored_comments.append({
                    "body": body,
                    "score": comment["score"] or 0,
                    "relevance": comment_relevance,
                })

        if relevance <= 0:
            continue

        # Sort comments by score (engagement) then relevance
        scored_comments.sort(key=lambda c: (c["score"], c["relevance"]), reverse=True)

        engagement = (post["score"] or 0) + (post["num_comments"] or 0) * 2
        # Relevance dominates ranking; engagement only matters as a tiebreaker
        # to avoid low-relevance viral posts outranking exact matches
        combined_score = relevance * 10.0 + min(engagement, relevance * 2.0)

        scored_results.append({
            "title": _clean_text(title),
            "flair": flair,
            "selftext": _clean_text(selftext),
            "url": post["url"] or "",
            "post_score": post["score"] or 0,
            "num_comments": post["num_comments"] or 0,
            "relevance": relevance,
            "combined_score": combined_score,
            "comments": [
                {"body": _clean_text(c["body"]), "score": c["score"]}
                for c in scored_comments
            ],
        })

    # Sort by combined score (relevance + engagement), highest first
    scored_results.sort(key=lambda r: r["combined_score"], reverse=True)
    return scored_results[:limit]


def _format_reddit_results(results: list[dict], query: str) -> str:
    """Format Reddit search results with enough context for the LLM to give advice."""
    if not results:
        return f'No relevant Reddit posts found for "{query}".'

    lines = [f'Found {len(results)} Reddit post(s) matching "{query}" (sorted by relevance and engagement):\n']
    for i, post in enumerate(results, 1):
        lines.append(f"--- Post {i} ---")
        lines.append(f"Title: {post['title']}")
        if post["flair"]:
            lines.append(f"Flair: {post['flair']}")
        if post["selftext"]:
            # Show up to 500 chars of selftext for richer context
            selftext = post["selftext"]
            lines.append(f"Body: {selftext[:500]}{'...' if len(selftext) > 500 else ''}")
        lines.append(f"Upvotes: {post.get('post_score', post.get('score', 0))} | Comments: {post['num_comments']}")
        lines.append(f"URL: {post['url']}")
        if post["comments"]:
            lines.append(f"Top Comments ({len(post['comments'])} relevant):")
            # Show up to 5 top comments with more text
            for j, c in enumerate(post["comments"][:5], 1):
                body = c["body"]
                score_str = f" [+{c['score']}]" if c.get("score", 0) > 0 else ""
                lines.append(f"  {j}. {body[:400]}{'...' if len(body) > 400 else ''}{score_str}")
        lines.append("")
    return "\n".join(lines)


@tool
def live_scrape_reddit(query: str, limit: int = 10) -> str:
    """
    Live scrape r/UFL for posts/comments relevant to the query (and course name if applicable),
    add new results to the Reddit database, and return formatted results.
    Warn users that this may take 1-2 minutes.

    IMPORTANT:
    - Whenever a Reddit post is mentioned or referenced in the response, ALWAYS include the Reddit post URL.
    - When a user asks about Reddit replies for a course or topic, return MULTIPLE posts with SHORT summaries (not a long discussion about a single post), unless only one post is most relevant or exactly matches the user's request.
    - This tool is LIMITED to scraping a MAXIMUM of 10 posts from Reddit per query.
    - Use the Reddit posts and comments as evidence to offer genuine advice and recommendations to the student — don't just summarize, actually help them make decisions.
    """
    expanded = _expand_query(query)
    all_results = []
    seen_ids = set()
    headers = {"User-Agent": "ufl-course-assistant-live-scraper/1.0"}

    # Search for each exact phrase (course code variants, course names, etc.)
    for phrase in expanded["exact_phrases"]:
        url = "https://www.reddit.com/r/UFL/search.json"
        params = {
            "q": phrase,
            "restrict_sr": 1,
            "sort": "relevance",
            "limit": 10
        }
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            continue
        for c in data.get("data", {}).get("children", []):
            p = c["data"]
            pid = p["id"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            post = {
                "id": pid,
                "name": p.get("name"),
                "title": p.get("title"),
                "selftext": p.get("selftext"),
                "author": p.get("author"),
                "created_utc": p.get("created_utc"),
                "score": p.get("score"),
                "num_comments": p.get("num_comments"),
                "flair": p.get("link_flair_text"),
                "permalink": p.get("permalink"),
                "url": p.get("url"),
                "comments": [],
                "raw": p
            }
            # Fetch top-level comments
            try:
                comm_url = f"https://www.reddit.com{p.get('permalink')}.json"
                comm_resp = requests.get(comm_url, headers=headers, timeout=15)
                comm_resp.raise_for_status()
                comm_data = comm_resp.json()
                if isinstance(comm_data, list) and len(comm_data) > 1:
                    for node in comm_data[1]["data"]["children"]:
                        if node.get("kind") == "t1":
                            d = node["data"]
                            comment = {
                                "id": d.get("id"),
                                "author": d.get("author"),
                                "body": d.get("body"),
                                "score": d.get("score"),
                                "created_utc": d.get("created_utc"),
                                "replies": []
                            }
                            post["comments"].append(comment)
            except Exception:
                pass
            all_results.append(post)
            if len(all_results) >= limit:
                break
        if len(all_results) >= limit:
            break
        time.sleep(2)  # avoid rate-limiting

    # --- Merge into SQLite database ---
    reddit_db.init_db()
    new_count = 0
    for post in all_results:
        if not reddit_db.post_exists(post["id"]):
            reddit_db.upsert_post(post)
            new_count += 1

    reddit_db.set_meta("last_updated", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    reddit_db.set_meta("total_posts", str(reddit_db.get_post_count()))

    # Sort live results by engagement before formatting
    all_results.sort(key=lambda p: (p.get("score", 0) or 0) + (p.get("num_comments", 0) or 0) * 2, reverse=True)

    # Sort comments within each post by score
    for p in all_results:
        if p.get("comments"):
            p["comments"].sort(key=lambda c: c.get("score", 0) or 0, reverse=True)

    formatted = _format_reddit_results([{
        "title": _clean_text(p.get("title", "")),
        "flair": p.get("flair", ""),
        "selftext": _clean_text(p.get("selftext", "")),
        "url": p.get("url", ""),
        "post_score": p.get("score", 0) or 0,
        "num_comments": p.get("num_comments", 0) or 0,
        "comments": [
            {"body": _clean_text(c.get("body", "")), "score": c.get("score", 0) or 0}
            for c in (p.get("comments") or [])
        ],
    } for p in all_results], query)

    return (
        f"Live Reddit scraping complete. {new_count} new post(s) added to the database.\n\n"
        + formatted
        + "\n\nNote: This tool may take 1-2 minutes to run and will update the Reddit database for future searches."
    )


@tool
def search_reddit(query: str, limit: int = 10) -> str:
    """
    Search r/UFL Reddit posts and comments for relevant information about UF courses,
    professors, majors, academic advice, or campus topics.

    Results are ranked by relevance and engagement (upvotes + comment activity), so
    the most useful and active threads appear first. Vulgar content is filtered out.

    Use this tool for:
    - Course opinions: "COP3530", "Data Structures"
    - Professor experiences: "professor John Smith", "Dr. Lee"
    - Major/program advice: "Computer Science major", "pre-med track"
    - General academic topics: "registration tips", "summer classes", "GPA advice"

    IMPORTANT:
    - Whenever a Reddit post is mentioned or referenced in the response, ALWAYS include the Reddit post URL.
    - When a user asks about Reddit replies for a course or topic, return MULTIPLE posts with SHORT summaries, unless only one post is most relevant.
    - Use the Reddit posts and comments as evidence to offer genuine advice and recommendations to the student — don't just summarize, actually help them make decisions based on the collective student experience.
    - This tool searches the cached Reddit database. Use live_scrape_reddit for the most up-to-date posts.

    Args:
        query: Course code, professor name, major, or topic keyword (e.g. "COP3530", "Dr. Smith", "Computer Science major", "registration").
        limit: Maximum number of posts to return (default 10).
    """
    results = _search_reddit(query, limit=limit)
    return _format_reddit_results(results, query)
