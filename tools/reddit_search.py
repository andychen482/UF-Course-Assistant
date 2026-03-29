"""
LangChain tool for searching Reddit posts/comments from r/UFL related to UF courses, majors, or topics.

Searches the UFL_master.json file for relevant posts and comments, scores them by
relevance and engagement, filters out vulgar content, and returns rich context
for the LLM to synthesize actionable advice.
"""

import json
import os
import re

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from langchain_core.tools import tool
from tools.course_search import search_courses_by_code
import requests
import time

# Path to the master Reddit data file
REDDIT_MASTER_PATH = os.path.join("scrapers", "reddit_scrapes", "master", "UFL_master.json")

# Vulgarity filter (expand as needed)
VULGAR_WORDS = [
    "fuck", "shit", "bitch", "asshole", "dick", "piss", "cunt", "bastard", "slut", "whore",
    "fag", "cock", "damn", "crap", "retard", "nigger", "nigga", "twat", "wank", "wanker"
]
VULGAR_PATTERN = re.compile(r"\b(" + "|".join(re.escape(word) for word in VULGAR_WORDS) + r")\b", re.IGNORECASE)

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


def _expand_query(query: str) -> dict:
    """
    Expand the query into multiple search forms and keywords.

    Returns a dict with:
      - exact_phrases: list of exact substrings to match (course code variants, full query)
      - keywords: set of meaningful tokens for fuzzy matching
      - course_name: resolved course name if query is a course code
    """
    result = {"exact_phrases": [], "keywords": set(), "course_name": None}

    query_stripped = query.strip()
    result["exact_phrases"].append(query_stripped)

    # Course code variants (e.g. COP3530, COP 3530, cop3530)
    code_nospace = query_stripped.replace(" ", "").upper()
    code_withspace = re.sub(r"([A-Z]+)(\d+)", r"\1 \2", code_nospace)
    for variant in [code_nospace, code_withspace, code_nospace.lower(), code_withspace.lower()]:
        if variant not in result["exact_phrases"]:
            result["exact_phrases"].append(variant)

    # Keywords from the query
    result["keywords"] = _tokenize(query_stripped)

    # Try to resolve course code to course name
    try:
        course_results = search_courses_by_code.invoke({"course_code": code_nospace})
        if isinstance(course_results, str):
            import ast
            try:
                course_results = ast.literal_eval(course_results)
            except Exception:
                course_results = []
        if isinstance(course_results, list):
            for course in course_results:
                name = course.get("name") or course.get("title")
                if name:
                    result["course_name"] = name
                    result["exact_phrases"].append(name)
                    result["keywords"].update(_tokenize(name))
                    break
    except Exception:
        pass

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


def _engagement_score(post: dict) -> float:
    """Composite engagement score: upvotes + weighted comment count."""
    return (post.get("score", 0) or 0) + (post.get("num_comments", 0) or 0) * 2


_reddit_data_cache = None
_reddit_data_cache_mtime = None

def _load_reddit_data():
    global _reddit_data_cache, _reddit_data_cache_mtime
    try:
        mtime = os.path.getmtime(REDDIT_MASTER_PATH)
    except Exception:
        return None
    if _reddit_data_cache is not None and _reddit_data_cache_mtime == mtime:
        return _reddit_data_cache
    if not os.path.exists(REDDIT_MASTER_PATH):
        _reddit_data_cache = None
        _reddit_data_cache_mtime = None
        return None
    with open(REDDIT_MASTER_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    _reddit_data_cache = data
    _reddit_data_cache_mtime = mtime
    return data


def _search_reddit(query: str, limit: int = 10) -> list[dict]:
    """
    Search UFL_master.json for posts/comments relevant to the query.

    Uses relevance scoring (exact phrase + keyword overlap) and ranks results
    by a combination of relevance and engagement (score + comments).
    """
    data = _load_reddit_data()
    if not data:
        return []

    expanded = _expand_query(query)
    scored_results = []

    for post in data.get("posts", {}).values():
        title = post.get("title") or ""
        selftext = post.get("selftext") or ""
        flair = post.get("flair") or ""

        # Score the post itself (title weighted highest)
        relevance = (
            _score_text_match(title, expanded) * 2.0
            + _score_text_match(selftext, expanded)
            + _score_text_match(flair, expanded) * 0.5
        )

        # Score and collect matching comments
        scored_comments = []
        for comment in post.get("comments", []) or []:
            body = comment.get("body") or ""
            comment_relevance = _score_text_match(body, expanded)
            if comment_relevance > 0:
                relevance += comment_relevance * 0.5
                scored_comments.append({
                    "body": body,
                    "score": comment.get("score", 0) or 0,
                    "relevance": comment_relevance,
                })

        if relevance <= 0:
            continue

        # Sort comments by score (engagement) then relevance
        scored_comments.sort(key=lambda c: (c["score"], c["relevance"]), reverse=True)

        engagement = _engagement_score(post)
        # Final ranking: relevance is primary, engagement is secondary
        combined_score = relevance * 5.0 + engagement

        scored_results.append({
            "title": _clean_text(title),
            "flair": flair,
            "selftext": _clean_text(selftext),
            "url": post.get("url", ""),
            "post_score": post.get("score", 0) or 0,
            "num_comments": post.get("num_comments", 0) or 0,
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
    append new results to UFL_master.json, and return formatted results.
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

    # --- Merge into UFL_master.json ---
    if os.path.exists(REDDIT_MASTER_PATH):
        with open(REDDIT_MASTER_PATH, "r", encoding="utf-8") as f:
            master = json.load(f)
    else:
        master = {"posts": {}, "meta": {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}

    new_count = 0
    for post in all_results:
        if post["id"] not in master["posts"]:
            master["posts"][post["id"]] = post
            new_count += 1

    master["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    master["meta"]["total_posts"] = len(master["posts"])

    with open(REDDIT_MASTER_PATH, "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)

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
