#!/usr/bin/env python3
"""
One-time migration: convert UFL_master.json to SQLite database.

Usage:
  python migrate_json_to_sqlite.py                     # migrate default paths
  python migrate_json_to_sqlite.py --verify             # compare JSON vs DB counts
  python migrate_json_to_sqlite.py --json X --db Y      # custom paths
"""

import json
import os
import sys
import argparse
import time

sys.path.insert(0, os.path.dirname(__file__))
import reddit_db

DEFAULT_JSON = os.path.join("reddit_scrapes", "master", "UFL_master.json")
DEFAULT_DB = reddit_db.DB_PATH


def migrate(json_path: str, db_path: str):
    print(f"Loading {json_path} ...")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    posts = data.get("posts", {})
    meta = data.get("meta", {})
    print(f"  {len(posts)} posts found in JSON")

    print(f"Initializing database at {db_path} ...")
    reddit_db.init_db(db_path)
    conn = reddit_db.get_connection(db_path)

    # Migrate in a single transaction for speed
    print("Migrating posts and comments ...")
    t0 = time.time()

    total_comments = 0
    post_list = list(posts.values())

    # Batch insert — manually manage the transaction for speed
    conn.execute("BEGIN")
    try:
        for i, post in enumerate(post_list):
            raw = post.get("raw")
            raw_json = json.dumps(raw, ensure_ascii=False) if raw else None

            conn.execute(
                """INSERT OR REPLACE INTO posts
                   (id, name, title, selftext, author, created_utc, score,
                    num_comments, flair, permalink, url, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    post["id"],
                    post.get("name"),
                    post.get("title") or "",
                    post.get("selftext") or "",
                    post.get("author"),
                    post.get("created_utc"),
                    post.get("score") or 0,
                    post.get("num_comments") or 0,
                    post.get("flair") or "",
                    post.get("permalink") or "",
                    post.get("url") or "",
                    raw_json,
                ),
            )

            comments = post.get("comments")
            if comments:
                comment_rows = reddit_db._flatten_comments(comments, post["id"])
                total_comments += len(comment_rows)
                if comment_rows:
                    conn.executemany(
                        """INSERT OR REPLACE INTO comments
                           (id, post_id, parent_comment_id, author, body, score, created_utc)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        comment_rows,
                    )

            if (i + 1) % 5000 == 0:
                print(f"  {i + 1}/{len(post_list)} posts migrated ...")

        # Migrate meta
        for key, value in meta.items():
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, str(value)),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    elapsed = time.time() - t0
    print(f"Migration complete in {elapsed:.1f}s")
    print(f"  Posts:    {len(post_list)}")
    print(f"  Comments: {total_comments}")

    # Optimize
    conn.execute("PRAGMA optimize")

    db_size = os.path.getsize(db_path)
    json_size = os.path.getsize(json_path)
    print(f"\n  JSON size: {json_size / 1024 / 1024:.1f} MB")
    print(f"  DB size:   {db_size / 1024 / 1024:.1f} MB")


def verify(json_path: str, db_path: str):
    print(f"Loading JSON from {json_path} ...")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    json_posts = data.get("posts", {})

    def _count_comments(comments):
        count = 0
        if not comments:
            return 0
        for c in comments:
            if isinstance(c, dict) and c.get("id"):
                count += 1
                count += _count_comments(c.get("replies") or [])
        return count

    json_comment_count = 0
    for post in json_posts.values():
        json_comment_count += _count_comments(post.get("comments"))

    reddit_db.init_db(db_path)
    conn = reddit_db.get_connection(db_path)
    db_post_count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    db_comment_count = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]

    print(f"\n{'':>20} {'JSON':>10} {'SQLite':>10} {'Match':>8}")
    print(f"{'-' * 52}")

    post_match = "OK" if len(json_posts) == db_post_count else "FAIL"
    print(f"{'Posts':>20} {len(json_posts):>10} {db_post_count:>10} {post_match:>8}")

    comment_match = "OK" if json_comment_count == db_comment_count else "FAIL"
    print(f"{'Comments':>20} {json_comment_count:>10} {db_comment_count:>10} {comment_match:>8}")

    # Spot-check a few posts
    import random
    sample_ids = random.sample(list(json_posts.keys()), min(10, len(json_posts)))
    mismatches = 0
    for pid in sample_ids:
        jp = json_posts[pid]
        dp = conn.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
        if not dp:
            print(f"  MISSING in DB: {pid}")
            mismatches += 1
            continue
        if jp.get("title", "") != (dp["title"] or ""):
            print(f"  Title mismatch for {pid}: {jp.get('title')!r} vs {dp['title']!r}")
            mismatches += 1
        if (jp.get("score") or 0) != (dp["score"] or 0):
            print(f"  Score mismatch for {pid}: {jp.get('score')} vs {dp['score']}")
            mismatches += 1

    if mismatches == 0:
        print(f"\nSpot-check: 10 random posts verified OK")
    else:
        print(f"\nSpot-check: {mismatches} issues found")

    if len(json_posts) == db_post_count and json_comment_count == db_comment_count and mismatches == 0:
        print("\nVerification PASSED")
    else:
        print("\nVerification FAILED")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Migrate UFL_master.json to SQLite")
    ap.add_argument("--json", default=DEFAULT_JSON, help="Path to source JSON")
    ap.add_argument("--db", default=DEFAULT_DB, help="Path to output SQLite DB")
    ap.add_argument("--verify", action="store_true", help="Verify JSON vs DB counts")
    args = ap.parse_args()

    if args.verify:
        verify(args.json, args.db)
    else:
        if os.path.exists(args.db):
            print(f"WARNING: {args.db} already exists. It will be overwritten.")
            resp = input("Continue? [y/N] ")
            if resp.lower() != "y":
                print("Aborted.")
                sys.exit(0)
            os.remove(args.db)
        migrate(args.json, args.db)


if __name__ == "__main__":
    main()
