#!/usr/bin/env python3
"""
Exhaustive Reddit scraper — retrieves ALL posts for given flairs with no cap.

Progress is NEVER lost:
  - Phase 1 saves posts to disk every batch with a resume cursor
  - Phase 2 scans comments in bulk with its own resume cursor
  - Ctrl-C saves before exit

Strategy:
  Phase 1: Discover posts via Pullpush (newest-first), client-side flair
           filter, save to master immediately with resume cursor.
  Phase 2: Bulk-fetch ALL subreddit comments from Pullpush, match to posts
           in master locally. Avoids Reddit 429s entirely.
  --reddit: Optionally supplement with Reddit search for very recent posts.

Usage
-----
  python reddit_full_scraper.py --start 2020-09-27
  python reddit_full_scraper.py --start 2020-09-27 --no-comments
  python reddit_full_scraper.py --start 2020-09-27 --reddit
  python reddit_full_scraper.py --start 2020-09-27 --pullpush-delay 0.7
"""

import requests
import json
import os
import time
import signal
import logging
import argparse
from datetime import datetime

# ────────────────────── CONFIG ──────────────────────
USER_AGENT = "script:ufl-full-scraper:4.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 5
DEFAULT_RATE_LIMIT = 1.5       # seconds between Reddit API calls
PULLPUSH_DELAY = 1.0           # seconds between Pullpush calls

PULLPUSH_SUBMISSIONS = "https://api.pullpush.io/reddit/search/submission/"
PULLPUSH_COMMENTS = "https://api.pullpush.io/reddit/search/comment/"
PULLPUSH_BATCH = 100

BASE_OUTPUT_DIR = "reddit_scrapes"
MASTER_DIR = os.path.join(BASE_OUTPUT_DIR, "master")
# ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reddit_full_scraper")


class RedditFullScraper:

    def __init__(self, subreddit, master_path=None, rate_limit=DEFAULT_RATE_LIMIT,
                 pullpush_delay=PULLPUSH_DELAY):
        self.subreddit = subreddit
        self.rate_limit = rate_limit
        self.pp_delay = pullpush_delay
        self.master_path = master_path or os.path.join(
            MASTER_DIR, f"{subreddit.upper()}_master.json"
        )

        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

        self.master = self._load_master()
        self._dirty = False
        self._saving = False
        self._req_count = 0
        self._shutdown = False

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._on_signal)
            except (OSError, ValueError):
                pass

    # ── master I/O ───────────────────────────────────

    def _load_master(self):
        os.makedirs(os.path.dirname(self.master_path), exist_ok=True)
        if os.path.exists(self.master_path):
            with open(self.master_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            log.info("Loaded master: %d existing posts", len(data.get("posts", {})))
            return data
        return {"posts": {}, "meta": {"created": datetime.utcnow().isoformat()}}

    def _save_master(self):
        if self._saving:
            return  # prevent re-entrant save from signal handler
        self._saving = True
        try:
            self.master["meta"]["last_updated"] = datetime.utcnow().isoformat()
            self.master["meta"]["total_posts"] = len(self.master["posts"])
            tmp = self.master_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.master, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.master_path)
            self._dirty = False
        finally:
            self._saving = False

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

    # ── Phase 1: post discovery (saves to disk!) ─────

    def _discover_and_save(self, flairs_lower, start_ts, end_ts):
        """
        Pullpush post discovery, newest-first.
        Saves matched posts to master EVERY BATCH with a resume cursor.
        On re-run, resumes from where it left off.
        """
        existing_ids = set(self.master["posts"].keys())

        # Resume support
        saved_cursor = self.master.get("meta", {}).get("discovery_cursor")
        if saved_cursor and start_ts < saved_cursor < end_ts:
            cursor = int(saved_cursor)
            log.info("  Resuming from %s (previous run saved cursor)", _ts(cursor))
        else:
            cursor = end_ts

        page = 0
        total_scanned = 0
        new_count = 0

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

            batch_new = 0
            for item in items:
                pid = item.get("id", "")
                if not pid:
                    continue
                total_scanned += 1

                if pid in existing_ids:
                    continue

                flair_text = (item.get("link_flair_text") or "").strip().lower()
                if flair_text in flairs_lower:
                    self.master["posts"][pid] = self._norm_post(item)
                    existing_ids.add(pid)
                    batch_new += 1
                    new_count += 1

            cursor = int(items[-1].get("created_utc", start_ts))

            # Save cursor + new posts every batch
            self.master["meta"]["discovery_cursor"] = cursor
            if batch_new > 0:
                self._dirty = True

            # Flush to disk every 5 pages (or when there are new posts)
            if page % 5 == 0 and self._dirty:
                self._save_master()

            if page % 10 == 0:
                log.info(
                    "  page %d | scanned %d | new %d | at %s",
                    page, total_scanned, new_count, _ts(cursor),
                )

            if len(items) < PULLPUSH_BATCH:
                break

        # Mark discovery complete if we reached the start
        if cursor <= start_ts and not self._shutdown:
            self.master["meta"].pop("discovery_cursor", None)
            self.master["meta"]["discovery_complete"] = True
            log.info("  Discovery complete: scanned %d, added %d posts", total_scanned, new_count)
        else:
            log.info("  Discovery paused at %s: scanned %d, added %d posts",
                     _ts(cursor), total_scanned, new_count)

        if self._dirty:
            self._save_master()

    # ── Phase 2: bulk comment fetch ──────────────────

    def _bulk_fetch_comments(self, start_ts, end_ts):
        """
        Scan ALL subreddit comments from Pullpush (not per-post!).
        Match top-level comments to posts in master by link_id.

        Uses comment_cursor for resume.  The scan is only "done" when the
        cursor reaches scan_start — until then, re-runs continue where
        they left off.
        """
        # Check if a previous scan already completed the full range
        saved_cursor = self.master.get("meta", {}).get("comment_cursor")
        scan_complete = self.master.get("meta", {}).get("comment_scan_done", False)

        if scan_complete and not saved_cursor:
            # Check if new posts were added since the scan finished
            has_none = any(p.get("comments") is None for p in self.master["posts"].values())
            if not has_none:
                log.info("  Comment scan already completed — nothing to do.")
                log.info("  (To force re-scan, delete 'comment_scan_done' from master meta)")
                return
            else:
                # New posts added — need a fresh scan
                log.info("  New posts found since last complete scan — restarting")
                scan_complete = False

        # Initialise any None comments to [] so we can append
        for post in self.master["posts"].values():
            if post.get("comments") is None:
                post["comments"] = []

        # Build dedup set from all existing comments (prevents doubles)
        existing_cids = set()
        for post in self.master["posts"].values():
            for c in (post.get("comments") or []):
                if isinstance(c, dict) and c.get("id"):
                    existing_cids.add(c["id"])

        # All post IDs in master (for matching)
        all_post_ids = set(self.master["posts"].keys())

        # Time range: cover all posts in master
        all_times = [p["created_utc"] for p in self.master["posts"].values()]
        scan_start = max(int(min(all_times)) - 86400, start_ts - 86400)
        scan_end = int(max(all_times)) + 86400 * 90
        scan_end = min(scan_end, int(datetime.utcnow().timestamp()))

        # Resume from saved cursor, or start from the top
        if saved_cursor and scan_start < saved_cursor < scan_end:
            cursor = int(saved_cursor)
            log.info("  Resuming comment scan from %s", _ts(cursor))
        else:
            cursor = scan_end

        log.info(
            "  Scanning comments: %s → %s  |  %d posts in master  |  %d comments already stored",
            _ts(cursor), _ts(scan_start), len(all_post_ids), len(existing_cids),
        )

        page = 0
        matched = 0
        scanned = 0

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

                # Match to a post in master
                link_id = (item.get("link_id") or "").replace("t3_", "")
                if link_id not in all_post_ids:
                    continue

                # Top-level comments only (parent is the post, not another comment)
                parent = str(item.get("parent_id", ""))
                if not parent.startswith("t3_"):
                    continue

                post = self.master["posts"][link_id]
                if not isinstance(post.get("comments"), list):
                    post["comments"] = []

                post["comments"].append({
                    "id": cid,
                    "author": item.get("author"),
                    "body": item.get("body"),
                    "score": item.get("score"),
                    "created_utc": item.get("created_utc"),
                })
                if cid:
                    existing_cids.add(cid)
                matched += 1

            cursor = int(items[-1].get("created_utc", scan_start))
            self.master["meta"]["comment_cursor"] = cursor
            self._dirty = True

            if page % 100 == 0:
                self._save_master()
                log.info(
                    "  scanned %dk comments | matched %d | at %s",
                    scanned // 1000, matched, _ts(cursor),
                )

            if len(items) < 100:
                break

        # Done?
        if cursor <= scan_start and not self._shutdown:
            self.master["meta"].pop("comment_cursor", None)
            self.master["meta"]["comment_scan_done"] = True
            log.info("  Comment scan COMPLETE: %d matched from %d scanned", matched, scanned)
        else:
            # Scan is NOT done — cursor saved for resume
            log.info("  Comment scan paused at %s: %d matched from %d scanned",
                     _ts(cursor), matched, scanned)
            log.info("  Re-run to continue from where it stopped.")

        if self._dirty:
            self._save_master()

    # ── Optional: Reddit search supplement ───────────

    def _reddit_supplement(self, flairs):
        existing_ids = set(self.master["posts"].keys())
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
            for pid, post in posts.items():
                if pid not in existing_ids:
                    self.master["posts"][pid] = post
                    existing_ids.add(pid)
                    added += 1
            if added:
                self._dirty = True
                self._save_master()
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
        log.info("Date range: %s → %s (newest first)", _ts(start_ts), _ts(end_ts))

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
            self._bulk_fetch_comments(start_ts, end_ts)

        log.info("")
        log.info("─" * 55)
        log.info("Master: %d total posts", len(self.master["posts"]))
        log.info("API requests: %d", self._req_count)
        log.info("File: %s", self.master_path)
        log.info("─" * 55)


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
    ap.add_argument("--master", help="Custom master JSON path")

    args = ap.parse_args()

    scraper = RedditFullScraper(
        subreddit=args.subreddit,
        master_path=args.master,
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
