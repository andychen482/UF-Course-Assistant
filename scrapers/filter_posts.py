#!/usr/bin/env python3
"""
Filter UFL_master.json to keep only posts relevant to courses, scheduling,
and graduation.

Keeps posts that:
  1. Have a target flair (Classes, Schedule, Graduation)
  2. Have no flair / wrong flair but match relevance signals (course codes,
     academic keywords)

Usage:
  python filter_posts.py                  # dry run — shows what would be removed
  python filter_posts.py --apply          # actually filter (backs up original first)
  python filter_posts.py --master X.json  # custom master path
"""

import json
import re
import os
import sys
import shutil
import logging
import argparse
from datetime import datetime

DEFAULT_MASTER = os.path.join("reddit_scrapes", "master", "UFL_master.json")

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


def filter_master(master_path: str, apply: bool = False):
    if not os.path.exists(master_path):
        log.error("File not found: %s", master_path)
        sys.exit(1)

    with open(master_path, "r", encoding="utf-8") as fh:
        master = json.load(fh)

    posts = master.get("posts", {})
    total = len(posts)

    keep = {}
    remove = {}
    reasons = {}

    for pid, post in posts.items():
        relevant, reason = is_relevant(post)
        if relevant:
            keep[pid] = post
            reasons[pid] = reason
        else:
            remove[pid] = post

    # ── stats ──
    reason_counts: dict = {}
    for r in reasons.values():
        bucket = r.split(":")[0]  # group by type
        reason_counts[bucket] = reason_counts.get(bucket, 0) + 1

    log.info("Total posts: %d", total)
    log.info("Keeping:     %d  (%.1f%%)", len(keep), 100 * len(keep) / max(total, 1))
    log.info("Removing:    %d  (%.1f%%)", len(remove), 100 * len(remove) / max(total, 1))
    log.info("")
    log.info("Kept-post breakdown:")
    for bucket, cnt in sorted(reason_counts.items(), key=lambda x: -x[1]):
        log.info("  %-14s %d", bucket, cnt)

    if remove:
        log.info("")
        log.info("Sample removed posts (first 10):")
        for pid in list(remove.keys())[:10]:
            p = remove[pid]
            log.info(
                "  [%s] flair=%-12s  %s",
                pid,
                (p.get("flair") or "—")[:12],
                (p.get("title") or "")[:65],
            )

    if apply:
        # Back up original (only first time)
        backup = master_path + ".pre-filter-backup"
        if not os.path.exists(backup):
            shutil.copy2(master_path, backup)
            log.info("")
            log.info("Original backed up to %s", backup)

        master["posts"] = keep
        master["meta"]["last_filtered"] = datetime.utcnow().isoformat()
        master["meta"]["total_posts"] = len(keep)
        master["meta"]["removed_by_filter"] = len(remove)

        tmp = master_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(master, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, master_path)

        log.info("Master filtered and saved: %d posts kept", len(keep))
    else:
        log.info("")
        log.info("DRY RUN — no changes made.  Use --apply to write.")


def main():
    ap = argparse.ArgumentParser(
        description="Filter master JSON for course/schedule/graduation relevance",
    )
    ap.add_argument("--master", default=DEFAULT_MASTER, help="Path to master JSON")
    ap.add_argument("--apply", action="store_true", help="Actually apply the filter")
    args = ap.parse_args()

    filter_master(args.master, apply=args.apply)


if __name__ == "__main__":
    main()
