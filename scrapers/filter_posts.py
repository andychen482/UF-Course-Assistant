#!/usr/bin/env python3
"""
Filter the Reddit SQLite database to keep only posts relevant to courses,
scheduling, and graduation.

Keeps posts that:
  1. Have a target flair (Classes, Schedule, Graduation)
  2. Have no flair / wrong flair but match relevance signals (course codes,
     academic keywords)

Usage:
  python filter_posts.py                  # dry run — shows what would be removed
  python filter_posts.py --apply          # actually filter
  python filter_posts.py --db X.db        # custom DB path
"""

import re
import sys
import logging
import argparse
from datetime import datetime

import reddit_db

# Flairs that are always kept
TARGET_FLAIRS = {"classes", "schedule", "graduation"}

# ── Relevance signals for unflaired / wrong-flair posts ──

# UF course codes: 3-4 letter prefix + 4-digit number + optional letter
COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,4}\s?\d{4}[A-Z]?\b", re.IGNORECASE)

RELEVANCE_KEYWORDS = [
    # Courses & classes
    "professor", "prof ", "instructor", "lecturer", "syllabus",
    "class", "classes", "course", "courses", "section",
    "midterm", "final exam", "exam ", "quiz", "homework", "assignment",
    "gpa", "grade", "grading", "credit hour", "credits",
    "prerequisite", "prereq", "corequisite", "coreq",
    "elective", "gen ed", "general education",
    "rate my professor", "ratemyprofessor",
    "easy a", "easy class", "hard class",
    "workload", "difficulty",
    "lecture", "lab section", "recitation",
    "curve", "curved",
    # Schedule / registration
    "schedule", "scheduling", "registration", "register",
    "drop/add", "drop add", "swap", "waitlist", "wait list",
    "one.uf", "one uf", "schedule of courses",
    "summer", "fall semester", "spring semester",
    "summer a", "summer b", "summer c",
    "online class", "in-person", "hybrid", "asynchronous",
    "time conflict",
    # Graduation
    "graduation", "graduating", "graduate",
    "degree", "major", "minor ",
    "critical tracking",
    "advisor", "advising", "academic advisor",
    "commencement", "diploma", "transcript",
    "degree audit",
    "four year plan", "four-year plan", "4 year plan",
    "apply to graduate", "intent to graduate",
    "bright futures", "financial aid",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("filter_posts")


def is_relevant(post: dict) -> tuple:
    """
    Returns (keep: bool, reason: str | None).
    """
    flair = (post.get("flair") or "").strip().lower()
    if flair in TARGET_FLAIRS:
        return True, f"flair:{flair}"

    # Check title + selftext + comments
    parts = [post.get("title", ""), post.get("selftext", "")]
    for c in post.get("comments", []) or []:
        parts.append(c.get("body", "") if isinstance(c, dict) else "")
    text = " ".join(parts).lower()

    # Course code match (strong signal)
    if COURSE_CODE_RE.search(text):
        return True, "course_code"

    # Keyword match
    for kw in RELEVANCE_KEYWORDS:
        if kw in text:
            return True, f"keyword:{kw}"

    return False, None


def filter_master(db_path: str = None, apply: bool = False):
    reddit_db.init_db(db_path)
    conn = reddit_db.get_connection(db_path)

    posts = conn.execute("SELECT id, title, selftext, flair FROM posts").fetchall()
    total = len(posts)

    keep_ids = []
    remove_ids = []
    reasons = {}

    for post in posts:
        pid = post["id"]
        # Fetch comments for relevance check
        comments = conn.execute(
            "SELECT body FROM comments WHERE post_id = ?", (pid,)
        ).fetchall()

        post_dict = {
            "title": post["title"],
            "selftext": post["selftext"],
            "flair": post["flair"],
            "comments": [{"body": c["body"]} for c in comments],
        }
        relevant, reason = is_relevant(post_dict)
        if relevant:
            keep_ids.append(pid)
            reasons[pid] = reason
        else:
            remove_ids.append(pid)

    # ── stats ──
    reason_counts: dict = {}
    for r in reasons.values():
        bucket = r.split(":")[0]  # group by type
        reason_counts[bucket] = reason_counts.get(bucket, 0) + 1

    log.info("Total posts: %d", total)
    log.info("Keeping:     %d  (%.1f%%)", len(keep_ids), 100 * len(keep_ids) / max(total, 1))
    log.info("Removing:    %d  (%.1f%%)", len(remove_ids), 100 * len(remove_ids) / max(total, 1))
    log.info("")
    log.info("Kept-post breakdown:")
    for bucket, cnt in sorted(reason_counts.items(), key=lambda x: -x[1]):
        log.info("  %-14s %d", bucket, cnt)

    if remove_ids:
        log.info("")
        log.info("Sample removed posts (first 10):")
        sample = remove_ids[:10]
        for pid in sample:
            p = conn.execute(
                "SELECT id, flair, title FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            if p:
                log.info(
                    "  [%s] flair=%-12s  %s",
                    p["id"],
                    (p["flair"] or "-")[:12],
                    (p["title"] or "")[:65],
                )

    if apply:
        reddit_db.delete_posts(remove_ids, db_path)
        reddit_db.set_meta("last_filtered", datetime.utcnow().isoformat(), db_path)
        reddit_db.set_meta("total_posts", str(len(keep_ids)), db_path)
        reddit_db.set_meta("removed_by_filter", str(len(remove_ids)), db_path)
        log.info("Database filtered: %d posts kept, %d removed", len(keep_ids), len(remove_ids))
    else:
        log.info("")
        log.info("DRY RUN — no changes made.  Use --apply to write.")


def main():
    ap = argparse.ArgumentParser(
        description="Filter Reddit DB for course/schedule/graduation relevance",
    )
    ap.add_argument("--db", default=None, help="Path to SQLite DB (default: standard location)")
    ap.add_argument("--apply", action="store_true", help="Actually apply the filter")
    args = ap.parse_args()

    filter_master(db_path=args.db, apply=args.apply)


if __name__ == "__main__":
    main()
