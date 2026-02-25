"""
LangChain tool for searching Reddit posts/comments from r/UFL related to UF courses, majors, or topics.

Searches the UFL_master.json file for relevant posts and comments, filters out vulgar content,
and returns concise summaries for the LLM to decide relevance.
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

def _clean_text(text: str) -> str:
    """Remove vulgar words and excessive whitespace from text."""
    text = VULGAR_PATTERN.sub("[redacted]", text)
    return re.sub(r"\s+", " ", text).strip()


# Helper to expand query with course name if possible
def _expand_query_with_course_name(query: str) -> list[str]:
    """If query is a course code, add the course name for better Reddit search."""
    queries = [query]
    # Try to resolve course code to name using search_courses_by_code
    code = query.strip().replace(" ", "").upper()
    try:
        course_results = search_courses_by_code.invoke({"query": code})
        if isinstance(course_results, str):
            import ast
            try:
                course_results = ast.literal_eval(course_results)
            except Exception:
                course_results = []
        if isinstance(course_results, list):
            for course in course_results:
                name = course.get("name") or course.get("title")
                if name and name.lower() not in [q.lower() for q in queries]:
                    queries.append(name)
    except Exception:
        pass
    return queries

def _search_reddit(query: str, limit: int = 10) -> list[dict]:
    """Search UFL_master.json for posts/comments relevant to the query or course name."""
    if not os.path.exists(REDDIT_MASTER_PATH):
        return []

    with open(REDDIT_MASTER_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = []
    queries = _expand_query_with_course_name(query)
    for post in data.get("posts", {}).values():
        fields = [
            post.get("title", ""),
            post.get("selftext", ""),
            post.get("flair", ""),
        ]
        found = False
        for q in queries:
            q_lower = q.lower()
            if any(q_lower in field.lower() for field in fields):
                found = True
                break
        if found:
            summary = {
                "title": _clean_text(post.get("title", "")),
                "flair": post.get("flair", ""),
                "selftext": _clean_text(post.get("selftext", "")),
                "url": post.get("url", ""),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "comments": [],
            }
            for comment in post.get("comments", []) or []:
                body = comment.get("body", "")
                if any(q.lower() in body.lower() for q in queries):
                    summary["comments"].append(_clean_text(body))
            results.append(summary)
            if len(results) >= limit:
                break
        else:
            for comment in post.get("comments", []) or []:
                body = comment.get("body", "")
                if any(q.lower() in body.lower() for q in queries):
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
    """
    queries = _expand_query_with_course_name(query)
    all_results = []
    seen_ids = set()
    for q in queries:
        url = "https://www.reddit.com/r/UFL/search.json"
        params = {
            "q": q,
            "restrict_sr": 1,
            "sort": "new",
            "limit": 10
        }
        headers = {"User-Agent": "ufl-course-assistant-live-scraper/1.0"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
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
            # Fetch top-level comments (optional, can be slow)
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

    return (
        f"Live Reddit scraping complete. {new_count} new post(s) added to the database.\n\n"
        + _format_reddit_results([{
            "title": _clean_text(p.get("title", "")),
            "flair": p.get("flair", ""),
            "selftext": _clean_text(p.get("selftext", "")),
            "url": p.get("url", ""),
            "score": p.get("score", 0),
            "num_comments": p.get("num_comments", 0),
            "comments": [_clean_text(c.get("body", "")) for c in (p.get("comments") or [])],
        } for p in all_results], query)
        + "\n\nNote: This tool may take 1-2 minutes to run and will update the Reddit database for future searches."
    )

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

    IMPORTANT:
    - Whenever a Reddit post is mentioned or referenced in the response, ALWAYS include the Reddit post URL.
    - When a user asks about Reddit replies for a course or topic, return MULTIPLE posts with SHORT summaries (not a long discussion about a single post), unless only one post is most relevant or exactly matches the user's request.
    - This tool is LIMITED to scraping a MAXIMUM of 10 posts from Reddit per live reddit scrape.

    Args:
        query: Course code, major, or topic keyword (e.g. "COP3530", "Computer Science", "registration").
        limit: Maximum number of posts to return (default 10).
    """
    results = _search_reddit(query, limit=limit)
    return _format_reddit_results(results, query)