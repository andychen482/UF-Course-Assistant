"""
LangChain tool for searching Reddit posts/comments from r/UFL related to UF courses, majors, or topics.

Searches the UFL_master.json file for relevant posts and comments, filters out vulgar content,
and returns concise summaries for the LLM to decide relevance.
"""

import json
import os
import re
from langchain_core.tools import tool

# Path to the master Reddit data file
REDDIT_MASTER_PATH = os.path.join("scrapers", "reddit_scrapes", "master", "UFL_master.json")

# Vulgarity filter (expand as needed)
VULGAR_WORDS = [
    "fuck", "shit", "bitch", "asshole", "dick", "piss", "cunt", "bastard", "slut", "whore",
    "fag", "cock", "damn", "crap", "retard", "nigger", "nigga", "twat", "wank", "wanker"
]
VULGAR_PATTERN = re.compile(r"\b(" + "|".join(re.escape(word) for word in VULGAR_WORDS) + r")\b", re.IGNORECASE)

def _clean_text(text: str) -> str:
    """Remove vulgar words and excessive whitespace from text."""
    text = VULGAR_PATTERN.sub("[redacted]", text)
    return re.sub(r"\s+", " ", text).strip()

def _search_reddit(query: str, limit: int = 10) -> list[dict]:
    """Search UFL_master.json for posts/comments relevant to the query."""
    if not os.path.exists(REDDIT_MASTER_PATH):
        return []

    with open(REDDIT_MASTER_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = []
    query_lower = query.lower()
    for post in data.get("posts", {}).values():
        # Search in post title, selftext, flair
        fields = [
            post.get("title", ""),
            post.get("selftext", ""),
            post.get("flair", ""),
        ]
        if any(query_lower in field.lower() for field in fields):
            summary = {
                "title": _clean_text(post.get("title", "")),
                "flair": post.get("flair", ""),
                "selftext": _clean_text(post.get("selftext", "")),
                "url": post.get("url", ""),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "comments": [],
            }
            # Search comments for relevance
            for comment in post.get("comments", []) or []:
                body = comment.get("body", "")
                if query_lower in body.lower():
                    summary["comments"].append(_clean_text(body))
            results.append(summary)
            if len(results) >= limit:
                break
        else:
            # Also search comments for relevance
            for comment in post.get("comments", []) or []:
                body = comment.get("body", "")
                if query_lower in body.lower():
                    summary = {
                        "title": _clean_text(post.get("title", "")),
                        "flair": post.get("flair", ""),
                        "selftext": _clean_text(post.get("selftext", "")),
                        "url": post.get("url", ""),
                        "score": post.get("score", 0),
                        "num_comments": post.get("num_comments", 0),
                        "comments": [_clean_text(body)],
                    }
                    results.append(summary)
                    if len(results) >= limit:
                        break
    return results

def _format_reddit_results(results: list[dict], query: str) -> str:
    """Format Reddit search results for LLM output."""
    if not results:
        return f'No relevant Reddit posts found for "{query}".'

    lines = [f'Found {len(results)} Reddit post(s) matching "{query}":\n']
    for i, post in enumerate(results, 1):
        lines.append(f"{i}. {post['title']} [{post['flair']}]")
        if post["selftext"]:
            lines.append(f"   {post['selftext'][:200]}{'...' if len(post['selftext']) > 200 else ''}")
        lines.append(f"   Score: {post['score']} | Comments: {post['num_comments']}")
        lines.append(f"   URL: {post['url']}")
        if post["comments"]:
            lines.append("   Relevant Comments:")
            for c in post["comments"]:
                lines.append(f"     - {c[:150]}{'...' if len(c) > 150 else ''}")
        lines.append("")
    return "\n".join(lines)

@tool
def search_reddit(query: str, limit: int = 10) -> str:
    """
    Search r/UFL Reddit posts and comments for relevant information about UF courses, majors, or topics.

    Filters out vulgar content and returns concise summaries for the LLM to decide relevance.

    Args:
        query: Course code, major, or topic keyword (e.g. "COP3530", "Computer Science", "registration").
        limit: Maximum number of posts to return (default 10).
    """
    results = _search_reddit(query, limit=limit)
    return _format_reddit_results(results, query)