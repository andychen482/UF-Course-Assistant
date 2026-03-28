#!/usr/bin/env python3
"""
GatorEvals Course Scraper
=========================
Scrapes evaluation scores for ALL ~7,037 courses from the GatorEvals
Tableau Public dashboard using a single Selenium Chrome session.

Filters by the "Combined Course" filter instead of "Instructor Name".
Each course is formatted as "<COURSECODE>,<COURSENAME>" in the dashboard.

Scrapes both the 10 question averages AND the course name in one pass.

Usage:
    python tools/gatorevals_course_scraper.py [--visible] [--delay 0.5]
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
# Constants
# ---------------------------------------------------------------------------

WORKBOOK = "GatorEvalsTableauPublic3TermstoFall2025"
VIEW = "GatorEvalsPublicDashboard"
BASE_URL = "https://public.tableau.com"
VIZQL_ROOT = f"{BASE_URL}/vizql/w/{WORKBOOK}/v/{VIEW}"
WORKSHEET = "GatorEvals Public Data"
DASHBOARD = "GatorEvals Public Dashboard"
FEDERATION_ID = "federated.0vmu5gw1jckesi1cbunbe10jfr46"
COURSE_FN = f"[{FEDERATION_ID}].[none:COMBINED_COURSE:nk]"

TABLEAU_EMBED_URL = (
    f"{BASE_URL}/views/{WORKBOOK}/{VIEW}"
    "?:embed=y&:showVizHome=no"
    "&:host_url=https%3A%2F%2Fpublic.tableau.com%2F"
    "&:embed_code_version=3&:tabs=no&:toolbar=yes"
    "&:animate_transition=yes&:display_static_image=no"
    "&:display_spinner=no&:display_overlay=yes"
    "&:display_count=yes&:language=en-US&:loadOrderID=0"
)

CHECKPOINT_FILE = "gatorevals_courses_checkpoint.json"
OUTPUT_CSV = "gatorevals_all_courses.csv"
SAVE_EVERY = 50

VALUE_LABELS = [
    "q1", "q2", "q3", "q4", "q5",
    "q6", "q7", "q8", "q9", "q10",
]


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
# Capture session_id, GSH, and bootstrap response in one log pass
# ---------------------------------------------------------------------------

def capture_all_session_data(
    driver: webdriver.Chrome, timeout: int = 60
) -> Tuple[str, str, str]:
    deadline = time.time() + timeout
    session_id = None
    gsh = None
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

                elif method == "Network.responseReceived":
                    resp = params.get("response", {})
                    url = resp.get("url", "")
                    if "bootstrapSession" in url and not bootstrap_request_id:
                        bootstrap_request_id = params.get("requestId")

            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        if session_id and gsh and bootstrap_request_id:
            bootstrap_text = ""
            try:
                body = driver.execute_cdp_cmd(
                    "Network.getResponseBody",
                    {"requestId": bootstrap_request_id},
                )
                bootstrap_text = body.get("body", "")
            except Exception as e:
                print(f"  [warn] getResponseBody failed: {e}")
            return session_id, gsh, bootstrap_text

        time.sleep(5)

    if session_id and gsh:
        print("  [warn] Bootstrap body not captured, using page source")
        try:
            return session_id, gsh, driver.page_source
        except Exception:
            return session_id, gsh, ""

    if session_id and not gsh:
        raise RuntimeError("Got session_id but no Global-Session-Header.")
    raise RuntimeError(f"Could not capture session info within {timeout}s.")


# ---------------------------------------------------------------------------
# Wait for Tableau render
# ---------------------------------------------------------------------------

def wait_for_bootstrap(driver: webdriver.Chrome, timeout: int = 60) -> None:
    js = """
    var done = false;
    if (document.querySelector('canvas')) done = true;
    if (document.querySelector('[id^="tabZoneId"]')) done = true;
    return done;
    """
    try:
        WebDriverWait(driver, timeout).until(lambda d: d.execute_script(js))
    except Exception:
        pass
    time.sleep(10)


# ---------------------------------------------------------------------------
# Parse course names from bootstrap
# ---------------------------------------------------------------------------

def parse_course_names(response_text: str) -> List[Tuple[int, str]]:
    text = response_text.replace('\\"', '"')
    for pattern in [
        r'"none:COMBINED_COURSE:nk".*?"tuples"\s*:\s*\[',
        r'"COMBINED_COURSE.*?"tuples"\s*:\s*\[',
    ]:
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            continue
        start = m.end() - 1
        tail = text[start:]
        depth, end = 0, None
        for i, ch in enumerate(tail):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if not end:
            continue
        block = tail[:end]
        raw_names = re.findall(r'"v"\s*:\s*"((?:\\.|[^"])*)"', block)
        if raw_names:
            names = [json.loads(f'"{n}"') for n in raw_names]
            return list(enumerate(names))
    return []


def get_actual_size(response_text: str) -> int:
    text = response_text.replace('\\"', '"')
    m = re.search(
        r'"none:COMBINED_COURSE:nk".*?"actual_size"\s*:\s*(\d+)',
        text, re.DOTALL,
    )
    if not m:
        m = re.search(r'"actual_size"\s*:\s*(\d+)', text)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Inject filter + scraper function into browser
# ---------------------------------------------------------------------------

# The JS does:
#  1. POSTs categorical-filter-by-index with filter-delta + GSH
#  2. Parses the JSON response IN THE BROWSER
#  3. Extracts the 10 question averages from dataValues
#  4. Extracts the course name from the selectionSummary (quick filter zone)
#     AND from cstring dataColumns as fallback
#  5. Returns {values: [...10 floats...], name: "...", error: null}

_JS_SCRAPER_FN = """
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

        // Strip size prefix (e.g. "195763;{...}")
        var semi = text.indexOf(';');
        if (semi !== -1 && semi < 20) {
            text = text.substring(semi + 1);
        }

        var obj;
        try {
            obj = JSON.parse(text);
        } catch(e) {
            return {error: 'json_parse_failed: ' + e.message, status: 0};
        }

        // ── Navigate the Tableau response structure ──────────────────
        var appModel = null;
        try {
            appModel = obj.vqlCmdResponse.layoutStatus.applicationPresModel;
        } catch(e) {}
        if (!appModel) {
            return {error: 'no_app_model', status: 0};
        }

        // ── Extract course name from selectionSummary ────────────────
        var courseName = null;
        try {
            var zones = appModel.workbookPresModel.dashboardPresModel.zones;
            for (var zid in zones) {
                try {
                    var qf = zones[zid].presModelHolder
                                .quickFilterDisplay.quickFilter;
                    if (qf.fn && qf.fn.indexOf('COMBINED_COURSE') !== -1) {
                        courseName = qf.selectionSummary || null;
                        break;
                    }
                } catch(e2) {}
            }
        } catch(e) {}

        // ── Collect ALL real (float) dataValues ──────────────────────
        var realDataValues = [];
        var baseOffset = -1;
        try {
            var dd = appModel.dataDictionary;
            if (!dd || !dd.dataSegments) {
                return {error: 'no_data_dictionary', status: 0, name: courseName};
            }
            var segs = dd.dataSegments;
            var segKeys = Object.keys(segs).sort(function(a,b) {
                return Number(a) - Number(b);
            });
            var cumulativeLen = 0;
            for (var s = 0; s < segKeys.length; s++) {
                var seg = segs[segKeys[s]];
                if (!seg) continue;
                var cols = seg.dataColumns || [];
                for (var c = 0; c < cols.length; c++) {
                    var dv = cols[c].dataValues || [];
                    if (cols[c].dataType === 'real') {
                        if (baseOffset === -1) baseOffset = cumulativeLen;
                        for (var v = 0; v < dv.length; v++) {
                            realDataValues.push(dv[v]);
                        }
                    }
                    cumulativeLen += dv.length;
                }
            }
        } catch(e) {
            return {error: 'no_data_segments: ' + e.message, status: 0, name: courseName};
        }

        if (realDataValues.length === 0) {
            return {error: 'no_real_data', status: 0, name: courseName};
        }

        // ── Find paneColumnsData ─────────────────────────────────────
        var paneColumnsData = null;
        try {
            var zones = appModel.workbookPresModel.dashboardPresModel.zones;
            for (var zid in zones) {
                try {
                    var vd = zones[zid].presModelHolder.visual.vizData;
                    if (vd && vd.paneColumnsData) {
                        paneColumnsData = vd.paneColumnsData;
                        break;
                    }
                } catch(e2) {}
            }
        } catch(e) {}

        if (!paneColumnsData) {
            return {error: 'no_pane_columns_data', status: 0, name: courseName};
        }

        var vizDataColumns = paneColumnsData.vizDataColumns || [];
        var paneColumnsList = paneColumnsData.paneColumnsList || [];

        // ── Find column index for AVG(Response Value) ────────────────
        var avgColIdx = -1;
        for (var i = 0; i < vizDataColumns.length; i++) {
            var cap = (vizDataColumns[i].fieldCaption || '').toUpperCase();
            if (cap.indexOf('AVG') !== -1 && cap.indexOf('RESPONSE') !== -1) {
                avgColIdx = i;
                break;
            }
        }
        if (avgColIdx === -1) {
            return {error: 'no_avg_column', status: 0, name: courseName};
        }

        // ── Compute base offset for real-valued global indices ───────
        var realColIndices = [];
        for (var i = 0; i < vizDataColumns.length; i++) {
            if (vizDataColumns[i].dataType === 'real') {
                realColIndices.push(i);
            }
        }

        var minRealGlobalIdx = Infinity;
        for (var p = 0; p < paneColumnsList.length; p++) {
            var vpCols = paneColumnsList[p].vizPaneColumns || [];
            for (var ri = 0; ri < realColIndices.length; ri++) {
                var ci = realColIndices[ri];
                if (ci >= vpCols.length) continue;
                var col = vpCols[ci];
                var indices = col.valueIndices || col.aliasIndices || [];
                for (var j = 0; j < indices.length; j++) {
                    if (indices[j] < minRealGlobalIdx) {
                        minRealGlobalIdx = indices[j];
                    }
                }
            }
        }

        if (minRealGlobalIdx === Infinity) {
            return {error: 'no_real_global_indices', status: 0, name: courseName};
        }

        var realBaseOffset = minRealGlobalIdx;

        // ── Extract the 10 AVG values ────────────────────────────────
        var avgValues = null;
        for (var p = 0; p < paneColumnsList.length; p++) {
            var vpCols = paneColumnsList[p].vizPaneColumns || [];
            if (avgColIdx >= vpCols.length) continue;
            var avgCol = vpCols[avgColIdx];
            var indices = avgCol.valueIndices || avgCol.aliasIndices || [];
            if (indices.length === 10) {
                avgValues = [];
                for (var j = 0; j < 10; j++) {
                    var localIdx = indices[j] - realBaseOffset;
                    if (localIdx >= 0 && localIdx < realDataValues.length) {
                        avgValues.push(realDataValues[localIdx]);
                    } else {
                        avgValues.push(null);
                    }
                }
                break;
            }
        }

        // ── Fallback: extract course name from cstring columns ───────
        if (!courseName) {
            try {
                var segs = appModel.dataDictionary.dataSegments;
                for (var sk in segs) {
                    if (!segs[sk]) continue;
                    var cols = segs[sk].dataColumns || [];
                    for (var c = 0; c < cols.length; c++) {
                        if (cols[c].dataType === 'cstring') {
                            var vals = cols[c].dataValues || [];
                            for (var v = 0; v < vals.length; v++) {
                                if (typeof vals[v] === 'string' &&
                                    vals[v].indexOf(',') !== -1 &&
                                    vals[v].length > 5 &&
                                    /^[A-Z]{2,4}\\d/.test(vals[v])) {
                                    courseName = vals[v];
                                    break;
                                }
                            }
                            if (courseName) break;
                        }
                    }
                    if (courseName) break;
                }
            } catch(e) {}
        }

        var ok = avgValues && avgValues.length === 10 &&
                 avgValues.every(function(v) { return v !== null; });
        return {
            values: avgValues,
            name: courseName,
            error: ok ? null
                 : (avgValues ? 'incomplete_' + avgValues.filter(
                     function(v){return v!==null;}).length : 'no_data'),
            numValues: avgValues ? avgValues.length : 0,
            _dbgRealDataLen: realDataValues.length,
            _dbgBaseOffset: realBaseOffset
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
        VIZQL_ROOT, session_id, gsh, WORKSHEET, DASHBOARD, COURSE_FN,
    )
    driver.execute_script(_JS_SCRAPER_FN)


# ---------------------------------------------------------------------------
# Execute a filter operation
# ---------------------------------------------------------------------------

def exec_filter(
    driver: webdriver.Chrome,
    add_indices: List[int],
    remove_indices: List[int],
    timeout: int = 45,
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
# Session init & recovery
# ---------------------------------------------------------------------------

def init_session(driver: webdriver.Chrome) -> Tuple[str, str, str]:
    driver.execute_cdp_cmd("Network.enable", {})
    try:
        driver.get_log("performance")
    except Exception:
        pass
    driver.get(TABLEAU_EMBED_URL)
    session_id, gsh, bootstrap_text = capture_all_session_data(driver, timeout=90)
    print(f"  Session ID: {session_id}")
    print(f"  GSH: {gsh}")
    print(f"  Bootstrap: {len(bootstrap_text)} chars")
    wait_for_bootstrap(driver, timeout=60)
    return session_id, gsh, bootstrap_text


def do_recovery(driver: webdriver.Chrome, target_idx: int) -> Tuple[str, str, dict]:
    print("\n  [recovery] Re-navigating for fresh session...")
    try:
        driver.get_log("performance")
    except Exception:
        pass
    driver.execute_cdp_cmd("Network.enable", {})
    driver.get(TABLEAU_EMBED_URL)
    session_id, gsh, _ = capture_all_session_data(driver, timeout=90)
    print(f"  [recovery] New session: {session_id}")
    wait_for_bootstrap(driver, timeout=60)
    inject_scraper(driver, session_id, gsh)

    exec_filter(driver, [], list(range(8000)), timeout=60)
    time.sleep(2)
    result = exec_filter(driver, [target_idx], [], timeout=30)
    return session_id, gsh, result


# ---------------------------------------------------------------------------
# Checkpoint & CSV
# ---------------------------------------------------------------------------

def load_checkpoint() -> Tuple[list, set]:
    if not os.path.exists(CHECKPOINT_FILE):
        return [], set()
    try:
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        results = data.get("results", [])
        completed = {r["idx"] for r in results if "idx" in r}
        print(f"  [resume] {len(results)} records from checkpoint")
        return results, completed
    except Exception:
        return [], set()


def save_checkpoint(results: list) -> None:
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({"results": results, "count": len(results)}, f, ensure_ascii=False)


def write_csv(results: list) -> None:
    if not results:
        print("No results to write.")
        return
    fieldnames = ["course_code", "course_name", "combined", "idx"] + VALUE_LABELS
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            combined = r.get("combined", "")
            code, name = "", ""
            if combined and "," in combined:
                code = combined.split(",", 1)[0].strip()
                name = combined.split(",", 1)[1].strip()
            row = {
                "course_code": code,
                "course_name": name,
                "combined": combined,
                "idx": r.get("idx", ""),
            }
            values = r.get("values")
            if values:
                for i, label in enumerate(VALUE_LABELS):
                    if i < len(values):
                        row[label] = round(values[i], 6)
            w.writerow(row)
    print(f"  CSV: {OUTPUT_CSV} ({len(results)} rows)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GatorEvals course scraper")
    parser.add_argument("--visible", action="store_true", help="Show browser")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Delay between requests (default: 0.5s)")
    args = parser.parse_args()

    headless = not args.visible
    delay = args.delay

    results, completed_indices = load_checkpoint()

    print("Launching Chrome...")
    driver = make_driver(headless=headless)

    try:
        # ── Phase 1: Bootstrap ──────────────────────────────────────────
        print("Navigating to Tableau dashboard...")
        session_id, gsh, bootstrap_text = init_session(driver)

        courses = parse_course_names(bootstrap_text)
        actual_size = get_actual_size(bootstrap_text)

        if not courses:
            print("\nERROR: Could not parse course names from bootstrap.")
            return

        print(f"  Parsed {len(courses)} names (actual_size={actual_size})")

        name_map = dict(courses)
        total = max(actual_size, len(courses)) if actual_size else len(courses)

        # Build work list: all indices not yet completed
        work_items = [i for i in range(total) if i not in completed_indices]

        if not work_items:
            print("All courses already completed!")
            write_csv(results)
            return

        print(f"  {len(work_items)} remaining to scrape (of {total} total)\n")

        # ── Phase 2: Setup filter ───────────────────────────────────────
        inject_scraper(driver, session_id, gsh)

        print("  Clearing filter (remove all indices)...")
        exec_filter(driver, [], list(range(8000)), timeout=60)
        time.sleep(3)

        print("  Setup: adding dummy index 0...")
        exec_filter(driver, [0], [], timeout=30)
        time.sleep(2)
        prev_idx = 0

        # ── Phase 3: Main scrape loop ──────────────────────────────────
        consecutive_errors = 0
        done_count = len(completed_indices)

        for work_pos, idx in enumerate(work_items):
            time.sleep(delay)

            # Two-step filter: remove old, then add new
            exec_filter(driver, [], [prev_idx], timeout=30)
            time.sleep(0.05)
            result = exec_filter(driver, [idx], [], timeout=30)

            status = result.get("status", 0)
            error = result.get("error")

            # Session expiry -> recover and retry
            if status in (410, 401, 403):
                print(f"\n  Session expired (HTTP {status}) at idx {idx}")
                session_id, gsh, result = do_recovery(driver, idx)
                prev_idx = idx
                status = result.get("status", 0)
                error = result.get("error")

            values = result.get("values")

            # Retry once on any error
            if (not values or len(values) < 10) and error:
                time.sleep(3)
                exec_filter(driver, [], [idx], timeout=30)
                time.sleep(1)
                result = exec_filter(driver, [idx], [], timeout=30)
                values = result.get("values")
                error = result.get("error")

            # Determine course name
            # Priority: response selectionSummary > bootstrap name > placeholder
            combined_name = result.get("name")
            if not combined_name:
                combined_name = name_map.get(idx)
            if not combined_name:
                combined_name = f"<index_{idx}>"
            else:
                # Cache for later
                name_map[idx] = combined_name

            if (values and isinstance(values, list) and len(values) >= 10
                    and all(v is not None for v in values)):
                consecutive_errors = 0
                row = {"combined": combined_name, "idx": idx, "values": values}
                results.append(row)
                completed_indices.add(idx)
                done_count += 1
                nv = len(values)
                vals_str = " ".join(f"{v:.2f}" for v in values)
                # Show course code portion for compact display
                display = combined_name[:55]
                print(
                    f"  [{done_count:>5}/{total}] idx={idx:<5} "
                    f"{display:<57} ({nv}q) {vals_str}"
                )
                sys.stdout.flush()
            else:
                consecutive_errors += 1
                results.append(
                    {"combined": combined_name, "idx": idx, "values": None,
                     "error": str(error)}
                )
                completed_indices.add(idx)
                done_count += 1
                display = combined_name[:55]
                print(
                    f"  [{done_count:>5}/{total}] idx={idx:<5} "
                    f"{display:<57} -- {error or 'no data'}"
                )
                sys.stdout.flush()

            prev_idx = idx

            if consecutive_errors >= 15:
                print("  [warn] 15 consecutive errors, recovering session...")
                session_id, gsh, _ = do_recovery(driver, idx)
                prev_idx = idx
                consecutive_errors = 0

            if done_count % SAVE_EVERY == 0:
                save_checkpoint(results)
                print(f"  [checkpoint] {done_count} records saved")

        # ── Phase 4: Final save ─────────────────────────────────────────
        save_checkpoint(results)
        write_csv(results)

        ok = sum(1 for r in results if r.get("values"))
        print(f"\nDone. {ok}/{len(results)} courses with data.")

    except KeyboardInterrupt:
        print("\n\nInterrupted. Saving checkpoint...")
        save_checkpoint(results)
        write_csv(results)
    except Exception as exc:
        print(f"\nFatal error: {exc}")
        import traceback
        traceback.print_exc()
        if results:
            save_checkpoint(results)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
