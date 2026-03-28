#!/usr/bin/env python3
"""
GatorEvals Name Scraper
========================
Maps every filter index (0–7342) to its instructor name by applying each
filter and reading the selectionSummary from zone 11 of the response.

Outputs: gatorevals_instructor_names.json  (dict of idx -> name)
         gatorevals_instructor_names.csv   (idx, name)

Usage:
    python scrapers/gatorevals/gatorevals_name_scraper.py [--visible] [--delay 0.8]
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from typing import List, Tuple

from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait

# ---------------------------------------------------------------------------
# Constants (same as bulk scraper)
# ---------------------------------------------------------------------------

WORKBOOK = "GatorEvalsTableauPublic3TermstoFall2025"
VIEW = "GatorEvalsPublicDashboard"
BASE_URL = "https://public.tableau.com"
VIZQL_ROOT = f"{BASE_URL}/vizql/w/{WORKBOOK}/v/{VIEW}"
WORKSHEET = "GatorEvals Public Data"
DASHBOARD = "GatorEvals Public Dashboard"
FEDERATION_ID = "federated.0vmu5gw1jckesi1cbunbe10jfr46"
INSTRUCTOR_FN = f"[{FEDERATION_ID}].[none:INSTRUCTOR_NAME:nk]"

TABLEAU_EMBED_URL = (
    f"{BASE_URL}/views/{WORKBOOK}/{VIEW}"
    "?:embed=y&:showVizHome=no"
    "&:host_url=https%3A%2F%2Fpublic.tableau.com%2F"
    "&:embed_code_version=3&:tabs=no&:toolbar=yes"
    "&:animate_transition=yes&:display_static_image=no"
    "&:display_spinner=no&:display_overlay=yes"
    "&:display_count=yes&:language=en-US&:loadOrderID=0"
)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(_SCRIPT_DIR, "gatorevals_instructor_names.json")
OUTPUT_CSV = os.path.join(_SCRIPT_DIR, "gatorevals_instructor_names.csv")
CHECKPOINT_FILE = os.path.join(_SCRIPT_DIR, "gatorevals_names_checkpoint.json")
SAVE_EVERY = 100


# ---------------------------------------------------------------------------
# Driver setup
# ---------------------------------------------------------------------------

def make_driver(headless: bool = True) -> webdriver.Chrome:
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=opts)


# ---------------------------------------------------------------------------
# Capture session_id and GSH
# ---------------------------------------------------------------------------

def capture_session(driver: webdriver.Chrome, timeout: int = 60) -> Tuple[str, str]:
    deadline = time.time() + timeout
    session_id = gsh = None

    while time.time() < deadline:
        try:
            logs = driver.get_log("performance")
        except Exception:
            time.sleep(1)
            continue

        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                method = msg.get("method", "")
                params = msg.get("params", {})

                if method == "Network.requestWillBeSent":
                    req = params.get("request", {})
                    url = req.get("url", "")
                    headers = req.get("headers", {})

                    if "bootstrapSession" in url and not session_id:
                        m = re.search(
                            r"sessions/([A-F0-9]{32,}-\d+:\d+)",
                            url, re.IGNORECASE,
                        )
                        if m:
                            session_id = m.group(1)

                    if not gsh:
                        for hname, hval in headers.items():
                            if hname.lower() == "global-session-header":
                                gsh = hval
                                break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        if session_id and gsh:
            return session_id, gsh
        time.sleep(0.5)

    raise RuntimeError(f"Could not capture session info within {timeout}s.")


# ---------------------------------------------------------------------------
# JS: filter function that extracts selectionSummary (instructor name)
# ---------------------------------------------------------------------------

_JS_NAME_SCRAPER = """
window._gatorFilter = function(addIdx, removeIdx) {
    var cfg = window._GE_CONFIG;
    var url = cfg.vizqlRoot + '/sessions/' + cfg.sessionId
            + '/commands/tabdoc/categorical-filter-by-index';

    var params = new URLSearchParams();
    params.append('visualIdPresModel',
        JSON.stringify({worksheet: cfg.worksheet, dashboard: cfg.dashboard}));
    params.append('globalFieldName', cfg.fieldName);
    params.append('membershipTarget', 'filter');
    params.append('filterUpdateType', 'filter-delta');
    params.append('filterAddIndices', JSON.stringify(addIdx));
    params.append('filterRemoveIndices', JSON.stringify(removeIdx));

    return fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'text/javascript',
            'global-session-header': cfg.gsh,
            'x-xsrf-token': 'null'
        },
        body: params.toString(),
        credentials: 'include'
    })
    .then(function(r) {
        if (!r.ok) return {error: 'HTTP ' + r.status, status: r.status};
        return r.text();
    })
    .then(function(text) {
        if (typeof text !== 'string') return text;

        // Strip size prefix
        var semi = text.indexOf(';');
        if (semi !== -1 && semi < 20) {
            text = text.substring(semi + 1);
        }

        var obj;
        try {
            obj = JSON.parse(text);
        } catch(e) {
            return {error: 'json_parse_failed', status: 0};
        }

        // Navigate to zones and find the one with INSTRUCTOR_NAME
        var name = null;
        try {
            var zones = obj.vqlCmdResponse.layoutStatus.applicationPresModel
                          .workbookPresModel.dashboardPresModel.zones;
            for (var zid in zones) {
                try {
                    var qf = zones[zid].presModelHolder
                                .quickFilterDisplay.quickFilter;
                    if (qf.fn && qf.fn.indexOf('INSTRUCTOR_NAME') !== -1) {
                        name = qf.selectionSummary || null;
                        break;
                    }
                } catch(e2) {}
            }
        } catch(e) {}

        return {
            name: name,
            error: name ? null : 'no_name_found',
            status: 0
        };
    })
    .catch(function(e) {
        return {error: e.toString(), status: 0};
    });
};
"""


def inject_scraper(driver: webdriver.Chrome, session_id: str, gsh: str) -> None:
    driver.execute_script(
        "window._GE_CONFIG = {"
        "  vizqlRoot: arguments[0],"
        "  sessionId: arguments[1],"
        "  gsh: arguments[2],"
        "  worksheet: arguments[3],"
        "  dashboard: arguments[4],"
        "  fieldName: arguments[5]"
        "};",
        VIZQL_ROOT, session_id, gsh, WORKSHEET, DASHBOARD, INSTRUCTOR_FN,
    )
    driver.execute_script(_JS_NAME_SCRAPER)


# ---------------------------------------------------------------------------
# Execute a filter operation
# ---------------------------------------------------------------------------

def exec_filter(
    driver: webdriver.Chrome,
    add_indices: List[int],
    remove_indices: List[int],
    timeout: int = 30,
) -> dict:
    driver.execute_script("window._gfr = null; window._gfe = null;")
    driver.execute_script(
        "var addIdx = arguments[0];"
        "var removeIdx = arguments[1];"
        "window._gatorFilter(addIdx, removeIdx)"
        "  .then(function(r) { window._gfr = r; })"
        "  .catch(function(e) { window._gfe = e.toString(); });",
        add_indices, remove_indices,
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.1)
        error = driver.execute_script("return window._gfe;")
        if error:
            return {"error": error, "status": 0}
        result = driver.execute_script("return window._gfr;")
        if result is not None:
            return result

    return {"error": "timeout", "status": 0}


# ---------------------------------------------------------------------------
# Bootstrap: get actual_size
# ---------------------------------------------------------------------------

def get_actual_size(driver: webdriver.Chrome, timeout: int = 60) -> int:
    """Get the actual_size from bootstrap by capturing the response."""
    deadline = time.time() + timeout
    bootstrap_request_id = None

    while time.time() < deadline:
        try:
            logs = driver.get_log("performance")
        except Exception:
            time.sleep(1)
            continue

        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                method = msg.get("method", "")
                params = msg.get("params", {})

                if method == "Network.responseReceived":
                    resp = params.get("response", {})
                    url = resp.get("url", "")
                    if "bootstrapSession" in url:
                        bootstrap_request_id = params.get("requestId")
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        if bootstrap_request_id:
            try:
                body = driver.execute_cdp_cmd(
                    "Network.getResponseBody",
                    {"requestId": bootstrap_request_id},
                )
                text = body.get("body", "")
                text = text.replace('\\"', '"')
                m = re.search(
                    r'"none:INSTRUCTOR_NAME:nk".*?"actual_size"\s*:\s*(\d+)',
                    text, re.DOTALL,
                )
                if m:
                    return int(m.group(1))
                m = re.search(r'"actual_size"\s*:\s*(\d+)', text)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
            return 7343  # fallback

        time.sleep(0.5)

    return 7343  # fallback


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if not os.path.exists(CHECKPOINT_FILE):
        return {}
    try:
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  [resume] {len(data)} names from checkpoint")
        return data
    except Exception:
        return {}


def save_checkpoint(name_map: dict) -> None:
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(name_map, f, ensure_ascii=False)


def write_outputs(name_map: dict) -> None:
    # JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(name_map, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {OUTPUT_JSON} ({len(name_map)} entries)")

    # CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["idx", "instructor"])
        for idx in sorted(name_map.keys(), key=int):
            w.writerow([idx, name_map[idx]])
    print(f"  CSV:  {OUTPUT_CSV} ({len(name_map)} entries)")


# ---------------------------------------------------------------------------
# Session recovery
# ---------------------------------------------------------------------------

def do_recovery(driver: webdriver.Chrome) -> Tuple[str, str]:
    print("\n  [recovery] Re-navigating for fresh session...")
    try:
        driver.get_log("performance")
    except Exception:
        pass
    driver.execute_cdp_cmd("Network.enable", {})
    driver.get(TABLEAU_EMBED_URL)
    session_id, gsh = capture_session(driver, timeout=60)
    print(f"  [recovery] New session: {session_id}")
    try:
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script(
                "return !!document.querySelector('canvas')"
            )
        )
    except Exception:
        pass
    time.sleep(10)
    inject_scraper(driver, session_id, gsh)
    exec_filter(driver, [], list(range(8000)), timeout=60)
    time.sleep(2)
    return session_id, gsh


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GatorEvals name scraper")
    parser.add_argument("--visible", action="store_true", help="Show browser")
    parser.add_argument("--delay", type=float, default=0.8,
                        help="Delay between requests (default: 0.8s)")
    args = parser.parse_args()

    headless = not args.visible
    delay = args.delay

    name_map = load_checkpoint()

    print("Launching Chrome...")
    driver = make_driver(headless=headless)

    try:
        # ── Phase 1: Bootstrap ────────────────────────────────────────
        print("Navigating to Tableau dashboard...")
        driver.execute_cdp_cmd("Network.enable", {})
        try:
            driver.get_log("performance")
        except Exception:
            pass

        driver.get(TABLEAU_EMBED_URL)
        session_id, gsh = capture_session(driver, timeout=60)
        print(f"  Session ID: {session_id}")
        print(f"  GSH: {gsh}")

        # Wait for render
        try:
            WebDriverWait(driver, 30).until(
                lambda d: d.execute_script(
                    "return !!document.querySelector('canvas')"
                )
            )
        except Exception:
            pass
        time.sleep(10)

        total = get_actual_size(driver, timeout=5)
        print(f"  Total instructors: {total}")

        # ── Phase 2: Setup ────────────────────────────────────────────
        inject_scraper(driver, session_id, gsh)

        print("  Clearing filter...")
        exec_filter(driver, [], list(range(8000)), timeout=60)
        time.sleep(2)

        # Build work list
        work_items = [i for i in range(total) if str(i) not in name_map]
        if not work_items:
            print("All names already collected!")
            write_outputs(name_map)
            return

        print(f"  {len(work_items)} remaining (of {total} total)\n")

        # ── Phase 3: Scrape names ─────────────────────────────────────
        consecutive_errors = 0
        done_count = len(name_map)
        prev_idx = -1  # nothing selected after clear

        for work_pos, idx in enumerate(work_items):
            time.sleep(delay)

            # Remove previous, then add current
            if prev_idx >= 0:
                exec_filter(driver, [], [prev_idx], timeout=30)
                time.sleep(0.1)
            result = exec_filter(driver, [idx], [], timeout=30)

            status = result.get("status", 0)
            error = result.get("error")
            name = result.get("name")

            # Session expiry -> recover
            if status in (410, 401, 403):
                print(f"\n  Session expired (HTTP {status}) at idx {idx}")
                session_id, gsh = do_recovery(driver)
                prev_idx = -1
                # Retry this index
                result = exec_filter(driver, [idx], [], timeout=30)
                name = result.get("name")
                error = result.get("error")

            # Retry once on failure
            if not name and error:
                time.sleep(2)
                if prev_idx >= 0:
                    exec_filter(driver, [], [idx], timeout=30)
                    time.sleep(0.5)
                result = exec_filter(driver, [idx], [], timeout=30)
                name = result.get("name")
                error = result.get("error")

            if name and name != "(All)":
                consecutive_errors = 0
                name_map[str(idx)] = name
                done_count += 1
                print(
                    f"  [{done_count:>5}/{total}] idx={idx:<5} {name}"
                )
                sys.stdout.flush()
            else:
                consecutive_errors += 1
                done_count += 1
                print(
                    f"  [{done_count:>5}/{total}] idx={idx:<5} "
                    f"-- {error or 'no name'}"
                )
                sys.stdout.flush()

            prev_idx = idx

            if consecutive_errors >= 20:
                print("  [warn] 20 consecutive errors, recovering...")
                session_id, gsh = do_recovery(driver)
                prev_idx = -1
                consecutive_errors = 0

            if done_count % SAVE_EVERY == 0:
                save_checkpoint(name_map)
                print(f"  [checkpoint] {done_count} done, {len(name_map)} names")

        # ── Phase 4: Save ─────────────────────────────────────────────
        save_checkpoint(name_map)
        write_outputs(name_map)
        print(f"\nDone. {len(name_map)} names collected out of {total}.")

    except KeyboardInterrupt:
        print("\n\nInterrupted. Saving checkpoint...")
        save_checkpoint(name_map)
        write_outputs(name_map)
    except Exception as exc:
        print(f"\nFatal error: {exc}")
        import traceback
        traceback.print_exc()
        if name_map:
            save_checkpoint(name_map)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
