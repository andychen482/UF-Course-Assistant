"""
LangChain tools for looking up GatorEvals evaluation data by instructor
or by course.

Reads pre-scraped evaluation scores from:
  - scrapers/gatorevals/gatorevals_all_instructors.csv  (instructor-level averages)
  - scrapers/gatorevals/gatorevals_all_courses.csv      (course-level averages)

Scale: 1 = Strongly Disagree … 5 = Strongly Agree
"""

import csv
import os
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scrapers", "gatorevals",
)
_CSV_PATH = os.path.join(_DATA_DIR, "gatorevals_all_instructors.csv")
_COURSE_CSV_PATH = os.path.join(_DATA_DIR, "gatorevals_all_courses.csv")

# Raw Tableau Public URL -- used internally by scrapers, kept for reference.
# _TABLEAU_URL = (
#     "https://public.tableau.com/views/"
#     "GatorEvalsTableauPublic3TermstoFall2025/GatorEvalsPublicDashboard"
# )

# UF-hosted page with the Tableau dashboard embedded (user-facing link).
_TABLEAU_URL = "https://gatorevals.aa.ufl.edu/public-results/"

_QUESTIONS = [
    "Overall, this course was a valuable educational experience",
    "Course activities improved ability to analyze, solve problems, think critically",
    "The course fostered regular interaction between student and instructor",
    "Course content (readings, activities, assignments) was relevant & useful",
    "The instructor was instrumental to my learning in the course",
    "The instructor provided prompt and meaningful feedback",
    "The instructor fostered a positive learning environment that engaged students",
    "The instructor maintained clear standards for response and availability",
    "The instructor explained material clearly and enhanced understanding",
    "The instructor was enthusiastic about the course",
]

# ---------------------------------------------------------------------------
# Data loading (lazy singleton)
# ---------------------------------------------------------------------------

_instructors: dict[str, dict] | None = None
_courses: dict[str, dict] | None = None


def _load_data() -> dict[str, dict]:
    """Load instructor CSV into a dict keyed by uppercase instructor name."""
    global _instructors
    if _instructors is not None:
        return _instructors

    _instructors = {}
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["instructor"].strip()
            if not name:
                continue
            scores = []
            for i in range(1, 11):
                val = row.get(f"q{i}", "").strip()
                scores.append(round(float(val), 2) if val else None)
            _instructors[name.upper()] = {
                "name": name,
                "idx": int(row["idx"]),
                "scores": scores,
            }
    return _instructors


def _load_courses() -> dict[str, dict]:
    """Load course CSV.

    Returns a dict keyed by uppercase course_code (e.g. "COP3530").
    Each value also stores the course_name so we can search by title.
    """
    global _courses
    if _courses is not None:
        return _courses

    _courses = {}
    with open(_COURSE_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["course_code"].strip()
            name = row["course_name"].strip()
            if not code:
                continue
            scores = []
            for i in range(1, 11):
                val = row.get(f"q{i}", "").strip()
                scores.append(round(float(val), 2) if val else None)
            entry = {
                "code": code,
                "name": name,
                "combined": row.get("combined", "").strip(),
                "idx": int(row["idx"]),
                "scores": scores,
            }
            _courses[code.upper()] = entry
    return _courses


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    """Normalize a name for comparison: uppercase, strip extra spaces."""
    return " ".join(name.upper().split())


def _match_instructor(query: str) -> list[dict]:
    """Find instructors matching the query.

    Supports:
      - Exact CSV-format match: "SMITH,JOHN D"
      - First Last format:      "John Smith" -> matches "SMITH,JOHN ..."
      - Partial / last-name-only searches
    """
    data = _load_data()
    q = _normalize(query)

    # 1. Exact match on CSV key
    if q in data:
        return [data[q]]

    # 2. Convert "First Last" to "LAST,FIRST" and try exact
    parts = q.split()
    if len(parts) >= 2:
        # Try "LAST,FIRST MIDDLE..." format
        last = parts[-1]
        first_parts = parts[:-1]
        csv_key = f"{last},{' '.join(first_parts)}"
        if csv_key in data:
            return [data[csv_key]]

    # 3. Fuzzy: find all entries where the query tokens appear in the name
    matches = []
    q_tokens = set(q.replace(",", " ").split())
    for key, entry in data.items():
        name_tokens = set(key.replace(",", " ").split())
        if q_tokens.issubset(name_tokens):
            matches.append(entry)

    if matches:
        return sorted(matches, key=lambda e: e["name"])[:5]

    # 4. Partial match: any token from query appears in the name
    partial = []
    for key, entry in data.items():
        name_upper = key.replace(",", " ")
        if all(tok in name_upper for tok in q_tokens):
            partial.append(entry)

    if partial:
        return sorted(partial, key=lambda e: e["name"])[:5]

    # 5. Last resort: substring match on last name (first token of CSV name)
    for key, entry in data.items():
        csv_last = key.split(",")[0]
        for tok in q_tokens:
            if tok == csv_last or csv_last.startswith(tok):
                partial.append(entry)
                break

    return sorted(partial, key=lambda e: e["name"])[:5]


# ---------------------------------------------------------------------------
# Course matching
# ---------------------------------------------------------------------------

def _match_course(query: str) -> list[dict]:
    """Find courses matching a course code or course name.

    Supports:
      - Exact code match: "COP3530"
      - Code with space: "COP 3530"
      - Partial code / prefix: "COP3"  or "COP"
      - Name keyword search: "Data Structures"
    """
    courses = _load_courses()
    q = _normalize(query)

    # 1. Exact code match (with or without space)
    q_nospace = q.replace(" ", "")
    if q_nospace in courses:
        return [courses[q_nospace]]

    # Also try the original (with space) in case CSV keys have spaces
    if q in courses:
        return [courses[q]]

    # 2. Code prefix match (e.g. "COP3" matches "COP3502", "COP3530" …)
    if q_nospace.replace("-", "").isalnum() and any(c.isdigit() for c in q_nospace):
        prefix_matches = [
            e for code, e in courses.items()
            if code.startswith(q_nospace)
        ]
        if prefix_matches:
            return sorted(prefix_matches, key=lambda e: e["code"])[:10]

    # 3. Name keyword search — every query token must appear in the course name
    q_tokens = set(q.replace(",", " ").split())
    name_matches = []
    for entry in courses.values():
        name_upper = entry["name"].upper()
        if all(tok in name_upper for tok in q_tokens):
            name_matches.append(entry)

    if name_matches:
        return sorted(name_matches, key=lambda e: e["code"])[:10]

    # 4. Partial: any query token appears in either code or name
    partial = []
    for code, entry in courses.items():
        combined = code + " " + entry["name"].upper()
        if any(tok in combined for tok in q_tokens):
            partial.append(entry)

    return sorted(partial, key=lambda e: e["code"])[:10]


# ---------------------------------------------------------------------------
# Tableau URL generation
# ---------------------------------------------------------------------------

def _tableau_url() -> str:
    """Return the GatorEvals Tableau dashboard link.

    Tableau Public URL parameter filtering doesn't work for instructor names
    because they contain commas (LASTNAME,FIRSTNAME) and Tableau treats
    commas as multi-value separators. So we link to the base dashboard and
    tell the user which name to select in the Instructor filter.
    """
    return _TABLEAU_URL


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _avg(scores: list) -> float | None:
    valid = [s for s in scores if s is not None]
    return round(sum(valid) / len(valid), 2) if valid else None


def _score_lines(scores: list) -> list[str]:
    """Return per-question lines for a score list."""
    lines = []
    for i, (question, score) in enumerate(zip(_QUESTIONS, scores), 1):
        val = f"{score}/5" if score is not None else "N/A"
        lines.append(f"  Q{i}. {question}")
        lines.append(f"      Score: {val}")
    return lines


_SCALE_LEGEND = (
    "  RATING SCALE: 1 = Strongly Disagree, 2 = Disagree, "
    "3 = Neutral, 4 = Agree, 5 = Strongly Agree\n"
    "  (Higher scores are better — they mean students agreed "
    "more favorably with each statement.)"
)


def _format_instructor_scores(entry: dict) -> str:
    """Format an instructor's GatorEvals scores into readable text."""
    scores = entry["scores"]
    has_data = any(s is not None for s in scores)

    lines = [f"GatorEvals — Instructor: {entry['name']}"]

    if not has_data:
        lines.append("  No evaluation data available for this instructor.")
        lines.append(f"  Tableau Dashboard: {_tableau_url()}")
        lines.append(f"  (Search for \"{entry['name']}\" in the Instructor filter)")
        return "\n".join(lines)

    lines.append(_SCALE_LEGEND)
    lines.append("")
    lines.extend(_score_lines(scores))

    overall = _avg(scores)
    if overall is not None:
        lines.append("")
        lines.append(f"  Average across all questions: {overall}/5")

    lines.append(f"\n  Tableau Dashboard: {_tableau_url()}")
    lines.append(f"  (Search for \"{entry['name']}\" in the Instructor filter)")
    return "\n".join(lines)


def _format_course_scores(entry: dict) -> str:
    """Format a course's GatorEvals scores into readable text."""
    scores = entry["scores"]
    has_data = any(s is not None for s in scores)
    label = f"{entry['code']} — {entry['name']}"

    lines = [f"GatorEvals — Course: {label}"]

    if not has_data:
        lines.append("  No evaluation data available for this course.")
        lines.append(f"  Tableau Dashboard: {_tableau_url()}")
        lines.append(f"  (Search for \"{entry['code']}\" in the Course filter)")
        return "\n".join(lines)

    lines.append(_SCALE_LEGEND)
    lines.append("")
    lines.extend(_score_lines(scores))

    overall = _avg(scores)
    if overall is not None:
        lines.append("")
        lines.append(f"  Average across all questions: {overall}/5")

    lines.append(f"\n  Tableau Dashboard: {_tableau_url()}")
    lines.append(f"  (Search for \"{entry['code']}\" in the Course filter)")
    return "\n".join(lines)


def _format_comparison(instructor: dict, course: dict) -> str:
    """Compare an instructor's scores against the course-wide average."""
    i_scores = instructor["scores"]
    c_scores = course["scores"]
    i_has = any(s is not None for s in i_scores)
    c_has = any(s is not None for s in c_scores)

    label_i = instructor["name"]
    label_c = f"{course['code']} — {course['name']}"

    lines = [
        f"GatorEvals Comparison",
        f"  Instructor: {label_i}",
        f"  Course:     {label_c}",
        "",
        _SCALE_LEGEND,
        "",
    ]

    if not i_has and not c_has:
        lines.append("  No evaluation data available for either the instructor or the course.")
        return "\n".join(lines)

    # Per-question comparison table
    for i, (question, i_s, c_s) in enumerate(
        zip(_QUESTIONS, i_scores, c_scores), 1
    ):
        i_str = f"{i_s}" if i_s is not None else "N/A"
        c_str = f"{c_s}" if c_s is not None else "N/A"

        diff_str = ""
        if i_s is not None and c_s is not None:
            diff = round(i_s - c_s, 2)
            sign = "+" if diff > 0 else ""
            diff_str = f"  ({sign}{diff})"

        lines.append(f"  Q{i}. {question}")
        lines.append(f"      Instructor: {i_str}   Course avg: {c_str}{diff_str}")

    # Overall summary
    i_avg = _avg(i_scores)
    c_avg = _avg(c_scores)
    lines.append("")
    if i_avg is not None and c_avg is not None:
        diff = round(i_avg - c_avg, 2)
        sign = "+" if diff > 0 else ""
        if diff > 0:
            verdict = "above"
        elif diff < 0:
            verdict = "below"
        else:
            verdict = "equal to"
        lines.append(
            f"  Instructor overall avg: {i_avg}/5  |  "
            f"Course overall avg: {c_avg}/5  |  "
            f"Diff: {sign}{diff}"
        )
        lines.append(
            f"  → This instructor scores {verdict} the course-wide average."
        )
    else:
        if i_avg is not None:
            lines.append(f"  Instructor overall avg: {i_avg}/5")
        if c_avg is not None:
            lines.append(f"  Course overall avg: {c_avg}/5")

    lines.append(f"\n  Tableau Dashboard: {_tableau_url()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LangChain Tools
# ---------------------------------------------------------------------------

@tool
def search_gatorevals(instructor_name: str) -> str:
    """Look up a UF instructor's GatorEvals teaching evaluation scores.

    GatorEvals is UF's official course evaluation system. Returns average
    scores (1-5 scale) for 10 evaluation questions covering course quality,
    instructor effectiveness, feedback, and enthusiasm. Also provides a
    direct link to the GatorEvals Tableau dashboard.

    Use this tool when a student asks about an instructor's teaching
    evaluations, GatorEvals scores, or how well an instructor teaches.
    Provide the instructor's name as it appears in the course catalog
    (e.g. "Amanpreet Kapoor" or "KAPOOR,AMANPREET").

    Args:
        instructor_name: The instructor's name (e.g. "Amanpreet Kapoor").
    """
    name = instructor_name.strip()
    if not name:
        return "Please provide an instructor name to search."

    matches = _match_instructor(name)

    if not matches:
        return (
            f'No GatorEvals data found for "{instructor_name}". '
            "The instructor may not have evaluations on file, or try a "
            "different name spelling. Names in the system are in "
            '"LASTNAME,FIRSTNAME" format (e.g. "SMITH,JOHN D").'
        )

    if len(matches) == 1:
        return _format_instructor_scores(matches[0])

    # Multiple matches
    lines = [
        f'Found {len(matches)} instructor(s) matching "{instructor_name}":',
        "",
    ]
    for entry in matches:
        avg = _avg(entry["scores"])
        avg_str = f"{avg}/5 avg" if avg else "No data"
        lines.append(f"  - {entry['name']} ({avg_str})")

    lines.append("")
    lines.append("Use the full name (e.g. the LASTNAME,FIRSTNAME format) for detailed scores.")

    return "\n".join(lines)


@tool
def search_gatorevals_course(query: str, instructor_name: str = "") -> str:
    """Look up GatorEvals evaluation scores for a UF course, and optionally
    compare them against a specific instructor's scores.

    Returns course-wide average scores (1-5 scale) for 10 evaluation
    questions.  When an instructor_name is also provided, shows a
    side-by-side comparison so the student can see whether that instructor
    scores above or below the course average.

    Use this tool when a student asks about evaluations for a specific
    course (by code or name), or when they ask how a professor compares
    to the course overall.

    Args:
        query: Course code (e.g. "COP3530", "COP 3530") or course name
               keywords (e.g. "Data Structures").
        instructor_name: Optional instructor name for comparison. If
                         provided, the response includes an instructor-vs-course
                         comparison.
    """
    q = query.strip()
    if not q:
        return "Please provide a course code or name to search."

    course_matches = _match_course(q)

    if not course_matches:
        return (
            f'No GatorEvals course data found for "{query}". '
            "Try the full course code (e.g. \"COP3530\") or a course "
            "name keyword (e.g. \"Data Structures\")."
        )

    # If instructor provided, attempt comparison with the best course match
    instr = instructor_name.strip()
    if instr:
        instr_matches = _match_instructor(instr)
        if instr_matches:
            # Use first course match and first instructor match
            return _format_comparison(instr_matches[0], course_matches[0])

    if len(course_matches) == 1:
        return _format_course_scores(course_matches[0])

    # Multiple course matches — list them
    lines = [
        f'Found {len(course_matches)} course(s) matching "{query}":',
        "",
    ]
    for entry in course_matches:
        avg = _avg(entry["scores"])
        avg_str = f"{avg}/5 avg" if avg else "No data"
        lines.append(f"  - {entry['code']} — {entry['name']} ({avg_str})")

    lines.append("")
    lines.append("Use the full course code for detailed scores.")
    return "\n".join(lines)
