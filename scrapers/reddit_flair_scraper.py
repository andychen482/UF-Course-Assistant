#!/usr/bin/env python3

import requests
import json
import time
import argparse
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict

# ---------- CONFIG ----------
USER_AGENT = "script:ufl-flair-scraper:1.2"
REQUEST_TIMEOUT = 15
DEFAULT_RATE_LIMIT = 1.0
MAX_RETRIES = 3

BASE_OUTPUT_DIR = "reddit_scrapes"
RUNS_DIR = os.path.join(BASE_OUTPUT_DIR, "runs")
MASTER_DIR = os.path.join(BASE_OUTPUT_DIR, "master")
MASTER_FILE_TEMPLATE = "{subreddit}_master.json"
# ----------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ---------- filesystem ----------
def ensure_dirs():
    for d in [BASE_OUTPUT_DIR, RUNS_DIR, MASTER_DIR]:
        os.makedirs(d, exist_ok=True)


# ---------- utils ----------
def epoch_from_iso(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())


def parse_flairs(flairs: List[str]) -> List[str]:  # NEW
    result = []
    for f in flairs:
        result.extend([x.strip() for x in f.split(",") if x.strip()])
    return result


def safe_request(url: str, params: dict = None):
    headers = {"User-Agent": USER_AGENT}
    backoff = 1.0
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logging.warning(f"Request failed ({attempt+1}/{MAX_RETRIES}): {e}")
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"Failed request: {url}")


# ---------- comments ----------
def parse_comment(node):
    if node.get("kind") != "t1":
        return None
    d = node["data"]
    comment = {
        "id": d.get("id"),
        "author": d.get("author"),
        "body": d.get("body"),
        "score": d.get("score"),
        "created_utc": d.get("created_utc"),
        "replies": []
    }
    replies = d.get("replies")
    if isinstance(replies, dict):
        for child in replies["data"]["children"]:
            parsed = parse_comment(child)
            if parsed:
                comment["replies"].append(parsed)
    return comment


def fetch_comments(permalink, rate_limit):
    url = f"https://www.reddit.com{permalink}.json"
    data = safe_request(url, params={"limit": 500})
    comments = []
    if isinstance(data, list) and len(data) > 1:
        for node in data[1]["data"]["children"]:
            parsed = parse_comment(node)
            if parsed:
                comments.append(parsed)
    time.sleep(rate_limit)
    return comments


# ---------- scraping ----------
def scrape_posts(subreddit, flair, since_ts, until_ts, max_posts, rate_limit):
    posts = {}
    after = None
    query = f'flair:"{flair}"'

    while True:
        params = {
            "q": query,
            "restrict_sr": 1,
            "sort": "new",
            "limit": 100
        }
        if after:
            params["after"] = after

        data = safe_request(f"https://www.reddit.com/r/{subreddit}/search.json", params)
        listing = data["data"]
        children = listing["children"]

        if not children:
            break

        page_valid = False
        for c in children:
            p = c["data"]
            created = p["created_utc"]
            if since_ts and created < since_ts:
                continue
            if until_ts and created > until_ts:
                continue

            page_valid = True
            pid = p["id"]
            if pid not in posts:
                posts[pid] = {
                    "id": pid,
                    "name": p["name"],
                    "title": p["title"],
                    "selftext": p["selftext"],
                    "author": p["author"],
                    "created_utc": created,
                    "score": p["score"],
                    "num_comments": p["num_comments"],
                    "flair": p["link_flair_text"],
                    "permalink": p["permalink"],
                    "url": p["url"],
                    "comments": None,
                    "raw": p
                }

            if max_posts and len(posts) >= max_posts:
                return list(posts.values())

        if not page_valid:
            break

        after = listing.get("after")
        if not after:
            break

        time.sleep(rate_limit)

    return list(posts.values())


# ---------- merge ----------
def merge_into_master(subreddit: str, new_posts: List[dict]):
    master_path = os.path.join(MASTER_DIR, MASTER_FILE_TEMPLATE.format(subreddit=subreddit))

    if os.path.exists(master_path):
        with open(master_path, "r", encoding="utf-8") as f:
            master = json.load(f)
    else:
        master = {"posts": {}, "meta": {"created": datetime.utcnow().isoformat()}}

    for post in new_posts:
        master["posts"][post["id"]] = post

    master["meta"]["last_updated"] = datetime.utcnow().isoformat()
    master["meta"]["total_posts"] = len(master["posts"])

    with open(master_path, "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)

    logging.info("Merged into master file: %s", master_path)


# ---------- orchestrator ----------
def run_for_flair(subreddit, flair, days, since, until, max_posts, rate_limit, merge):
    ensure_dirs()

    now = int(datetime.utcnow().timestamp())
    if days:
        since_ts = now - days * 86400
        until_ts = now
    else:
        since_ts = epoch_from_iso(since) if since else None
        until_ts = epoch_from_iso(until) + 86399 if until else None

    logging.info("Scraping flair: %s", flair)
    posts = scrape_posts(subreddit, flair, since_ts, until_ts, max_posts, rate_limit)

    logging.info("Fetching comments for %d posts", len(posts))
    for i, post in enumerate(posts, 1):
        post["comments"] = fetch_comments(post["permalink"], rate_limit)
        if i % 10 == 0:
            logging.info("Comments fetched: %d/%d", i, len(posts))

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_file = f"{subreddit}_{flair.replace(' ', '_')}_{timestamp}.json"
    run_path = os.path.join(RUNS_DIR, run_file)

    with open(run_path, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {
                "subreddit": subreddit,
                "flair": flair,
                "since_ts": since_ts,
                "until_ts": until_ts,
                "scraped_at": timestamp
            },
            "posts": posts
        }, f, indent=2, ensure_ascii=False)

    logging.info("Saved run snapshot: %s", run_path)

    if merge:
        merge_into_master(subreddit, posts)


# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subreddit", required=True)
    ap.add_argument("--flairs", nargs="+", required=True, help="Comma or space separated flairs")  # MODIFIED
    ap.add_argument("--days", type=int)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--max-posts", type=int)
    ap.add_argument("--rate-limit", type=float, default=DEFAULT_RATE_LIMIT)
    ap.add_argument("--merge", action="store_true")

    args = ap.parse_args()
    flairs = parse_flairs(args.flairs)

    for flair in flairs:
        run_for_flair(
            subreddit=args.subreddit,
            flair=flair,
            days=args.days,
            since=args.since,
            until=args.until,
            max_posts=args.max_posts,
            rate_limit=args.rate_limit,
            merge=args.merge
        )


if __name__ == "__main__":
    main()
