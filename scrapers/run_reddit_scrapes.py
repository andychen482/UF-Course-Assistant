#!/usr/bin/env python3

import subprocess
import json
import os
from datetime import datetime, timedelta
import logging

# ---------- CONFIG ----------
SCRAPER = "reddit_flair_scraper.py"
SUBREDDIT = "UFL"
FLAIRS = ["Classes", "Schedule", "Graduation"]

STATE_DIR = "reddit_scrapes/state"
INITIAL_STATE = os.path.join(STATE_DIR, "initial_progress.json")
DAILY_STATE = os.path.join(STATE_DIR, "daily_progress.json")

RATE_LIMIT = "2.0"
# ----------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def ensure_dirs():
    os.makedirs(STATE_DIR, exist_ok=True)


def load_state(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_state(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def run_scraper(flair, since, until):
    cmd = [
        "python", SCRAPER,
        "--subreddit", SUBREDDIT,
        "--flairs", flair,
        "--since", since,
        "--until", until,
        "--rate-limit", RATE_LIMIT,
        "--merge"
    ]
    logging.info("Running: %s", " ".join(cmd))
    subprocess.check_call(cmd)


# ---------- INITIAL BACKFILL ----------
def run_initial():
    ensure_dirs()
    state = load_state(INITIAL_STATE)

    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=365 * 3)

    for flair in FLAIRS:
        flair_start = state.get(SUBREDDIT, {}).get(flair)
        if flair_start:
            current = datetime.strptime(flair_start, "%Y-%m-%d").date()
        else:
            current = start_date

        while current < end_date:
            month_end = min(
                (current.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1),
                end_date
            )

            try:
                run_scraper(
                    flair,
                    since=current.isoformat(),
                    until=month_end.isoformat()
                )

                state.setdefault(SUBREDDIT, {})[flair] = (
                    month_end + timedelta(days=1)
                ).isoformat()
                save_state(INITIAL_STATE, state)

            except subprocess.CalledProcessError:
                logging.error("Failed at %s → %s for flair %s", current, month_end, flair)
                save_state(INITIAL_STATE, state)
                return

            current = month_end + timedelta(days=1)

    logging.info("Initial scrape complete.")


# ---------- DAILY UPDATE ----------
def run_daily():
    ensure_dirs()
    state = load_state(DAILY_STATE)

    now = datetime.utcnow().date()
    since = (now - timedelta(days=3)).isoformat()
    until = now.isoformat()

    for flair in FLAIRS:
        try:
            run_scraper(flair, since, until)
        except subprocess.CalledProcessError:
            logging.error("Daily scrape failed for flair %s", flair)
            return

    state["last_run"] = datetime.utcnow().isoformat()
    save_state(DAILY_STATE, state)
    logging.info("Daily update complete.")


# ---------- ENTRY ----------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["initial", "daily"])
    args = ap.parse_args()

    if args.mode == "initial":
        run_initial()
    else:
        run_daily()
