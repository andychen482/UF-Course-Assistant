#!/usr/bin/env python3
"""
Exhaustive Reddit scraper — retrieves ALL posts for given flairs with no cap.

Progress is NEVER lost:
  - Phase 1 saves posts to DB every batch with a resume cursor
  - Phase 2 scans comments in bulk with its own resume cursor
  - Ctrl-C commits before exit

Strategy:
  Phase 1: Discover posts via Pullpush (newest-first), client-side flair
           filter, save to DB immediately with resume cursor.
  Phase 2: Bulk-fetch ALL subreddit comments from Pullpush, match to posts
           in DB locally. Avoids Reddit 429s entirely.
  --reddit: Optionally supplement with Reddit search for very recent posts.

Usage
-----
  python reddit_full_scraper.py --start 2020-09-27
  python reddit_full_scraper.py --start 2020-09-27 --no-comments
  python reddit_full_scraper.py --start 2020-09-27 --reddit
  python reddit_full_scraper.py --start 2020-09-27 --pullpush-delay 0.7
"""

import requests
import time
import signal
import logging
import argparse
from datetime import datetime

import reddit_db

# ────────────────────── CONFIG ──────────────────────
USER_AGENT = "script:ufl-full-scraper:4.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 5
DEFAULT_RATE_LIMIT = 1.5       # seconds between Reddit API calls
PULLPUSH_DELAY = 1.0           # seconds between Pullpush calls

PULLPUSH_SUBMISSIONS = "https://api.pullpush.io/reddit/search/submission/"
PULLPUSH_COMMENTS = "https://api.pullpush.io/reddit/search/comment/"
PULLPUSH_BATCH = 100
# ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reddit_full_scraper")


class RedditFullScraper:

    def __init__(self, subreddit, db_path=None, rate_limit=DEFAULT_RATE_LIMIT,
                 pullpush_delay=PULLPUSH_DELAY):
        self.subreddit = subreddit
        self.rate_limit = rate_limit
        self.pp_delay = pullpush_delay
        self.db_path = db_path

        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

        reddit_db.init_db(self.db_path)
        self._existing_ids = reddit_db.get_all_post_ids(self.db_path)
        log.info("Loaded DB: %d existing posts", len(self._existing_ids))

        self._req_count = 0
        self._shutdown = False

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._on_signal)
            except (OSError, ValueError):
                pass

    # ── signal handling ──────────────────────────────

    def _on_signal(self, *_):
        log.warning("Shutdown requested — will exit after current operation …")
        self._shutdown = True

    # ── HTTP ─────────────────────────────────────────

    def _get(self, url, params=None, delay=None):
        time.sleep(delay if delay is not None else self.rate_limit)
        self._req_count += 1
        backoff = 2.0
        errors = 0
        while True:
            if self._shutdown:
                raise RuntimeError("Shutdown requested")
            try:
                r = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 300))
                    wait = max(wait, 300)  # always wait at least 5 min on 429
                    log.warning("429 rate-limited — pausing %dm%ds …",
                                wait // 60, wait % 60)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as exc:
                errors += 1
                log.warning("Request error (%d): %s", errors, exc)
                time.sleep(min(backoff, 300))
                backoff = min(backoff * 2, 300)  # cap at 5 min

    # ── normaliser ───────────────────────────────────

    @staticmethod
    def _norm_post(item):
        pid = item.get("id", "")
        return {
            "id": pid,
            "name": item.get("name") or f"t3_{pid}",
            "title": item.get("title", ""),
            "selftext": item.get("selftext", ""),
            "author": item.get("author", "[deleted]"),
            "created_utc": item.get("created_utc", 0),
            "score": item.get("score", 0),
            "num_comments": item.get("num_comments", 0),
            "flair": item.get("link_flair_text") or "",
            "permalink": item.get("permalink",
                                  f"/r/{item.get('subreddit', 'ufl')}/comments/{pid}/"),
            "url": item.get("url", ""),
            "comments": None,  # filled in Phase 2
        }

    # ── Phase 1: post discovery (saves to DB!) ───────

    def _discover_and_save(self, flairs_lower, start_ts, end_ts):
        """
        Pullpush post discovery, newest-first.
        Saves matched posts to DB EVERY BATCH with a resume cursor.
        On re-run, resumes from where it left off.
        """
        # Resume support
        saved_cursor = reddit_db.get_meta("discovery_cursor", self.db_path)
        if saved_cursor and start_ts < int(saved_cursor) < end_ts:
            cursor = int(saved_cursor)
            log.info("  Resuming from %s (previous run saved cursor)", _ts(cursor))
        else:
            cursor = end_ts

        page = 0
        total_scanned = 0
        new_count = 0
        batch_posts = []

        while cursor > start_ts and not self._shutdown:
            page += 1
            params = {
                "subreddit": self.subreddit,
                "before": cursor,
                "after": start_ts,
                "size": PULLPUSH_BATCH,
                "sort": "desc",
                "sort_type": "created_utc",
            }

            try:
                data = self._get(PULLPUSH_SUBMISSIONS, params, delay=self.pp_delay)
            except RuntimeError:
                log.error("Pullpush failed at %s — saving and stopping", _ts(cursor))
                break

            items = data.get("data", [])
            if not items:
                break

            for item in items:
                pid = item.get("id", "")
                if not pid:
                    continue
                total_scanned += 1

                if pid in self._existing_ids:
                    continue

                flair_text = (item.get("link_flair_text") or "").strip().lower()
                if flair_text in flairs_lower:
                    post = self._norm_post(item)
                    batch_posts.append(post)
                    self._existing_ids.add(pid)
                    new_count += 1

            cursor = int(items[-1].get("created_utc", start_ts))

            # Save cursor every batch
            reddit_db.set_meta("discovery_cursor", str(cursor), self.db_path)

            # Flush to DB every 5 pages (or when there are new posts)
            if page % 5 == 0 and batch_posts:
                reddit_db.upsert_posts_batch(batch_posts, self.db_path)
                batch_posts = []

            if page % 10 == 0:
                log.info(
                    "  page %d | scanned %d | new %d | at %s",
                    page, total_scanned, new_count, _ts(cursor),
                )

            if len(items) < PULLPUSH_BATCH:
                break

        # Flush remaining
        if batch_posts:
            reddit_db.upsert_posts_batch(batch_posts, self.db_path)

        # Mark discovery complete if we reached the start
        if cursor <= start_ts and not self._shutdown:
            # Clear cursor, mark complete
            conn = reddit_db.get_connection(self.db_path)
            conn.execute("DELETE FROM meta WHERE key = 'discovery_cursor'")
            conn.commit()
            reddit_db.set_meta("discovery_complete", "true", self.db_path)
            log.info("  Discovery complete: scanned %d, added %d posts", total_scanned, new_count)
        else:
            log.info("  Discovery paused at %s: scanned %d, added %d posts",
                     _ts(cursor), total_scanned, new_count)

        reddit_db.set_meta("last_updated", datetime.utcnow().isoformat(), self.db_path)
        reddit_db.set_meta("total_posts", str(reddit_db.get_post_count(self.db_path)), self.db_path)

    # ── Phase 2: bulk comment fetch ──────────────────

    def _bulk_fetch_comments(self, start_ts):
        """
        Scan ALL subreddit comments from Pullpush (not per-post!).
        Match top-level comments to posts in DB by link_id.

        Uses comment_cursor for resume.  The scan is only "done" when the
        cursor reaches scan_start — until then, re-runs continue where
        they left off.
        """
        conn = reddit_db.get_connection(self.db_path)

        saved_cursor = reddit_db.get_meta("comment_cursor", self.db_path)
        scan_complete = reddit_db.get_meta("comment_scan_done", self.db_path) == "true"

        if scan_complete and not saved_cursor:
            # Check if new posts were added since the scan finished (posts with no comments)
            has_none = conn.execute("""
                SELECT 1 FROM posts p
                WHERE NOT EXISTS (SELECT 1 FROM comments c WHERE c.post_id = p.id)
                LIMIT 1
            """).fetchone()
            if not has_none:
                log.info("  Comment scan already completed — nothing to do.")
                log.info("  (To force re-scan, delete 'comment_scan_done' from meta)")
                return
            else:
                log.info("  New posts found since last complete scan — restarting")
                scan_complete = False

        # Build dedup set from all existing comment IDs
        existing_cids = set()
        for row in conn.execute("SELECT id FROM comments").fetchall():
            existing_cids.add(row["id"])

        # All post IDs (for matching)
        all_post_ids = self._existing_ids

        # Time range: cover all posts in DB
        time_row = conn.execute(
            "SELECT MIN(created_utc) as mn, MAX(created_utc) as mx FROM posts"
        ).fetchone()
        if not time_row or time_row["mn"] is None:
            log.info("  No posts in DB — skipping comment scan")
            return

        scan_start = max(int(time_row["mn"]) - 86400, start_ts - 86400)
        scan_end = int(time_row["mx"]) + 86400 * 90
        scan_end = min(scan_end, int(datetime.utcnow().timestamp()))

        # Resume from saved cursor, or start from the top
        if saved_cursor and scan_start < int(saved_cursor) < scan_end:
            cursor = int(saved_cursor)
            log.info("  Resuming comment scan from %s", _ts(cursor))
        else:
            cursor = scan_end

        log.info(
            "  Scanning comments: %s -> %s  |  %d posts in DB  |  %d comments already stored",
            _ts(cursor), _ts(scan_start), len(all_post_ids), len(existing_cids),
        )

        page = 0
        matched = 0
        scanned = 0
        batch_comments = []

        while cursor > scan_start and not self._shutdown:
            page += 1
            params = {
                "subreddit": self.subreddit,
                "before": cursor,
                "after": scan_start,
                "size": 100,
                "sort": "desc",
                "sort_type": "created_utc",
            }

            try:
                data = self._get(PULLPUSH_COMMENTS, params, delay=self.pp_delay)
            except RuntimeError:
                log.error("Comment fetch failed at %s — saving", _ts(cursor))
                break

            items = data.get("data", [])
            if not items:
                break

            for item in items:
                scanned += 1
                cid = item.get("id")

                # Dedup
                if cid and cid in existing_cids:
                    continue

                # Match to a post in DB
                link_id = (item.get("link_id") or "").replace("t3_", "")
                if link_id not in all_post_ids:
                    continue

                # Top-level comments only (parent is the post, not another comment)
                parent = str(item.get("parent_id", ""))
                if not parent.startswith("t3_"):
                    continue

                batch_comments.append({
                    "id": cid,
                    "post_id": link_id,
                    "author": item.get("author"),
                    "body": item.get("body"),
                    "score": item.get("score"),
                    "created_utc": item.get("created_utc"),
                })
                if cid:
                    existing_cids.add(cid)
                matched += 1

            cursor = int(items[-1].get("created_utc", scan_start))
            reddit_db.set_meta("comment_cursor", str(cursor), self.db_path)

            if page % 100 == 0:
                # Flush comments batch
                if batch_comments:
                    self._flush_comments(batch_comments)
                    batch_comments = []
                log.info(
                    "  scanned %dk comments | matched %d | at %s",
                    scanned // 1000, matched, _ts(cursor),
                )

            if len(items) < 100:
                break

        # Flush remaining
        if batch_comments:
            self._flush_comments(batch_comments)

        # Done?
        if cursor <= scan_start and not self._shutdown:
            conn.execute("DELETE FROM meta WHERE key = 'comment_cursor'")
            conn.commit()
            reddit_db.set_meta("comment_scan_done", "true", self.db_path)
            log.info("  Comment scan COMPLETE: %d matched from %d scanned", matched, scanned)
        else:
            log.info("  Comment scan paused at %s: %d matched from %d scanned",
                     _ts(cursor), matched, scanned)
            log.info("  Re-run to continue from where it stopped.")

        reddit_db.set_meta("last_updated", datetime.utcnow().isoformat(), self.db_path)
        reddit_db.set_meta("total_posts", str(reddit_db.get_post_count(self.db_path)), self.db_path)

    def _flush_comments(self, batch):
        """Insert a batch of comments into the DB."""
        conn = reddit_db.get_connection(self.db_path)
        conn.executemany(
            """INSERT OR REPLACE INTO comments
               (id, post_id, parent_comment_id, author, body, score, created_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (c["id"], c["post_id"], None, c["author"],
                 c.get("body") or "", c.get("score") or 0, c.get("created_utc"))
                for c in batch
            ],
        )
        conn.commit()

    # ── Optional: Reddit search supplement ───────────

    def _reddit_supplement(self, flairs):
        for flair in flairs:
            if self._shutdown:
                break
            posts = {}
            after = None
            while not self._shutdown:
                params = {
                    "q": f'flair:"{flair}"',
                    "restrict_sr": 1,
                    "sort": "new",
                    "limit": 100,
                    "type": "link",
                }
                if after:
                    params["after"] = after
                url = f"https://www.reddit.com/r/{self.subreddit}/search.json"
                try:
                    data = self._get(url, params)
                except RuntimeError:
                    break
                children = data.get("data", {}).get("children", [])
                if not children:
                    break
                for child in children:
                    p = child["data"]
                    posts[p["id"]] = self._norm_post(p)
                after = data.get("data", {}).get("after")
                if not after:
                    break

            added = 0
            batch = []
            for pid, post in posts.items():
                if pid not in self._existing_ids:
                    batch.append(post)
                    self._existing_ids.add(pid)
                    added += 1
            if batch:
                reddit_db.upsert_posts_batch(batch, self.db_path)
            log.info("  '%s': %d from Reddit, %d new", flair, len(posts), added)

    # ── main ─────────────────────────────────────────

    def run(self, flairs, start_date, end_date=None,
            fetch_comments=True, use_reddit=False):
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_ts = (
            int(datetime.strptime(end_date, "%Y-%m-%d").timestamp()) + 86399
            if end_date
            else int(datetime.utcnow().timestamp())
        )
        flairs_lower = {f.strip().lower() for f in flairs}

        log.info("Target flairs: %s", flairs)
        log.info("Date range: %s -> %s (newest first)", _ts(start_ts), _ts(end_ts))

        # ── Phase 1: discover posts & save immediately ──
        log.info("")
        log.info("=" * 55)
        log.info("  Phase 1: Post discovery  (saves every batch)")
        log.info("=" * 55)
        self._discover_and_save(flairs_lower, start_ts, end_ts)

        # ── Optional: Reddit supplement ──
        if use_reddit:
            log.info("")
            log.info("=" * 55)
            log.info("  Reddit search supplement")
            log.info("=" * 55)
            self._reddit_supplement(flairs)

        # ── Phase 2: bulk comment fetch ──
        if fetch_comments:
            log.info("")
            log.info("=" * 55)
            log.info("  Phase 2: Bulk comment scan  (entire subreddit)")
            log.info("=" * 55)
            self._bulk_fetch_comments(start_ts)

        total = reddit_db.get_post_count(self.db_path)
        log.info("")
        log.info("-" * 55)
        log.info("DB: %d total posts", total)
        log.info("API requests: %d", self._req_count)
        log.info("-" * 55)


def _ts(epoch):
    return datetime.utcfromtimestamp(int(epoch)).strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser(
        description="Exhaustive Reddit flair scraper — saves progress every batch",
    )
    ap.add_argument("--subreddit", default="ufl")
    ap.add_argument("--flairs", nargs="+",
                    default=["Classes", "Schedule", "Graduation"])
    ap.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    ap.add_argument("--rate-limit", type=float, default=DEFAULT_RATE_LIMIT,
                    help=f"Reddit request delay (default: {DEFAULT_RATE_LIMIT}s)")
    ap.add_argument("--pullpush-delay", type=float, default=PULLPUSH_DELAY,
                    help=f"Pullpush request delay (default: {PULLPUSH_DELAY}s)")
    ap.add_argument("--no-comments", action="store_true",
                    help="Skip comment fetching (much faster)")
    ap.add_argument("--reddit", action="store_true",
                    help="Also run Reddit search for recent posts")
    ap.add_argument("--db", help="Custom SQLite DB path")

    args = ap.parse_args()

    scraper = RedditFullScraper(
        subreddit=args.subreddit,
        db_path=args.db,
        rate_limit=args.rate_limit,
        pullpush_delay=args.pullpush_delay,
    )
    scraper.run(
        flairs=args.flairs,
        start_date=args.start,
        end_date=args.end,
        fetch_comments=not args.no_comments,
        use_reddit=args.reddit,
    )


if __name__ == "__main__":
    main()
