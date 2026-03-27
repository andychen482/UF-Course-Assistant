"""
LangChain tool for looking up instructor evaluation data from GatorEvals.

Reads pre-scraped evaluation scores from gatorevals_all_instructors.csv and
provides instant lookups with fuzzy name matching. Also generates a direct
link to the Tableau dashboard filtered to the instructor.

Scale: 1 = Strongly Disagree … 5 = Strongly Agree
"""

import csv
import os
from urllib.parse import quote
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSV_PATH = os.path.join(_DATA_DIR, "gatorevals_all_instructors.csv")

_TABLEAU_BASE = (
    "https://public.tableau.com/views/"
    "GatorEvalsTableauPublic3TermstoFall2025/GatorEvalsPublicDashboard"
)

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


def _load_data() -> dict[str, dict]:
    """Load CSV into a dict keyed by uppercase instructor name."""
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
# Tableau URL generation
# ---------------------------------------------------------------------------

def _tableau_url(instructor_name: str) -> str:
    """Generate a Tableau Public URL filtered to the given instructor."""
    encoded = quote(instructor_name, safe="")
    return f"{_TABLEAU_BASE}?INSTRUCTOR%20NAME={encoded}"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_scores(entry: dict) -> str:
    """Format an instructor's GatorEvals scores into readable text."""
    scores = entry["scores"]
    has_data = any(s is not None for s in scores)

    lines = [f"GatorEvals: {entry['name']}"]

    if not has_data:
        lines.append("  No evaluation data available for this instructor.")
        lines.append(f"  Tableau Dashboard: {_tableau_url(entry['name'])}")
        return "\n".join(lines)

    lines.append("  Scale: 1 (Strongly Disagree) to 5 (Strongly Agree)")
    lines.append("")

    valid_scores = [s for s in scores if s is not None]
    overall_avg = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else None

    for i, (question, score) in enumerate(zip(_QUESTIONS, scores), 1):
        if score is not None:
            lines.append(f"  Q{i}. {question}")
            lines.append(f"      Score: {score}/5")
        else:
            lines.append(f"  Q{i}. {question}")
            lines.append(f"      Score: N/A")

    if overall_avg is not None:
        lines.append("")
        lines.append(f"  Average across all questions: {overall_avg}/5")

    lines.append(f"\n  Tableau Dashboard: {_tableau_url(entry['name'])}")

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
    direct link to the GatorEvals Tableau dashboard filtered to the instructor.

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
        return _format_scores(matches[0])

    # Multiple matches
    lines = [
        f'Found {len(matches)} instructor(s) matching "{instructor_name}":',
        "",
    ]
    for entry in matches:
        scores = entry["scores"]
        valid = [s for s in scores if s is not None]
        avg = round(sum(valid) / len(valid), 2) if valid else None
        avg_str = f"{avg}/5 avg" if avg else "No data"
        lines.append(f"  - {entry['name']} ({avg_str})")

    lines.append("")
    lines.append("Use the full name (e.g. the LASTNAME,FIRSTNAME format) for detailed scores.")

    return "\n".join(lines)
