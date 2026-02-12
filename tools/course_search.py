"""
LangChain tools for searching UF courses and retrieving section details.

Provides two tools:
  - search_courses: Search by course code or name, returns compact summaries.
  - get_course_sections: Get full section details for a specific course code.
"""

from langchain_core.tools import tool

from tools.course_data import get_courses, search_by_code, search_by_name


# ---------------------------------------------------------------------------
# Delivery-mode mapping
# ---------------------------------------------------------------------------
_DELIVERY_MODE = {
    "PC": "In-Person",
    "AD": "Online (Async)",
    "PD": "Partially Online",
    "HB": "Hybrid",
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_course_summary(course: dict, index: int) -> str:
    """Format a single course for the search_courses result list."""
    code_spaced = course.get("codeWithSpace", course["code"])
    name = course["name"]
    description = course.get("description", "").strip()
    prerequisites = course.get("prerequisites", "").strip()
    sections = course.get("sections", [])
    num_sections = len(sections)

    # Derive credits and department from the first section
    dept = ""
    credits = ""
    if sections:
        s0 = sections[0]
        dept = s0.get("deptName", "")
        cmin = s0.get("credits_min", s0.get("credits", ""))
        cmax = s0.get("credits_max", s0.get("credits", ""))
        credits = str(cmin) if cmin == cmax else f"{cmin}-{cmax}"

    lines = [f"{index}. {code_spaced} - {name}"]
    meta_parts = []
    if credits:
        meta_parts.append(f"Credits: {credits}")
    meta_parts.append(f"Sections: {num_sections}")
    if dept:
        meta_parts.append(f"Dept: {dept}")
    lines.append("   " + " | ".join(meta_parts))

    if description:
        # Truncate long descriptions to keep results compact
        desc = description if len(description) <= 200 else description[:197] + "..."
        lines.append(f"   Description: {desc}")
    if prerequisites:
        lines.append(f"   {prerequisites}")

    return "\n".join(lines)


def _format_meet_time(mt: dict) -> str:
    """Format a single meetTime entry into a readable schedule string."""
    days = "".join(mt.get("meetDays", []))
    building = mt.get("meetBuilding", "")
    room = mt.get("meetRoom", "")
    begin = mt.get("meetTimeBegin", "")
    end = mt.get("meetTimeEnd", "")
    period_begin = mt.get("meetPeriodBegin", "")
    period_end = mt.get("meetPeriodEnd", "")

    if not days and not begin:
        return "TBA"

    parts = []
    if days:
        parts.append(days)
    if period_begin and period_end:
        period_str = period_begin if period_begin == period_end else f"{period_begin}-{period_end}"
        parts.append(f"Period {period_str}")
    if begin and end:
        parts.append(f"({begin}-{end})")
    if building and room:
        parts.append(f"at {building} {room}")
    elif building:
        parts.append(f"at {building}")

    return " ".join(parts)


def _format_section(section: dict) -> str:
    """Format a single section for the get_course_sections result."""
    number = section.get("number", "?")
    class_num = section.get("classNumber", "?")
    delivery = _DELIVERY_MODE.get(section.get("sectWeb", ""), section.get("sectWeb", "Unknown"))

    instructors = ", ".join(
        inst.get("name", "TBA") for inst in section.get("instructors", [])
    ) or "TBA"

    meet_times = section.get("meetTimes", [])
    if meet_times:
        schedule_lines = [_format_meet_time(mt) for mt in meet_times]
        schedule = "; ".join(schedule_lines)
    else:
        schedule = "No fixed meeting times"

    credits_min = section.get("credits_min", section.get("credits", ""))
    credits_max = section.get("credits_max", section.get("credits", ""))
    credits = str(credits_min) if credits_min == credits_max else f"{credits_min}-{credits_max}"

    final_exam = section.get("finalExam", "").strip()
    note = section.get("note", "").strip()
    gen_ed = section.get("genEd", [])
    quest = section.get("quest", [])
    open_seats = section.get("openSeats")
    waitlist = section.get("waitList", {})
    wl_total = waitlist.get("total", 0)
    wl_cap = waitlist.get("cap", 0)
    fee = section.get("courseFee", 0)

    lines = [f"  Section {number} (Class# {class_num}) - {delivery}"]
    lines.append(f"    Instructor: {instructors}")
    lines.append(f"    Credits: {credits}")
    lines.append(f"    Schedule: {schedule}")
    if final_exam:
        lines.append(f"    Final Exam: {final_exam}")
    if open_seats is not None:
        lines.append(f"    Open Seats: {open_seats} | Waitlist: {wl_total}/{wl_cap}")
    if fee:
        lines.append(f"    Course Fee: ${fee:.2f}")
    if gen_ed:
        lines.append(f"    Gen Ed: {', '.join(gen_ed)}")
    if quest:
        lines.append(f"    Quest: {', '.join(quest)}")
    if note:
        lines.append(f"    Note: {note}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LangChain Tools
# ---------------------------------------------------------------------------

@tool
def search_courses(query: str) -> str:
    """Search for UF courses by course code or course name.

    Use this tool to find courses when you know a course code (e.g. "COP3530",
    "COP", "MAC 2311") or a course name keyword (e.g. "Data Structures",
    "Calculus"). Returns a summary list of matching courses with their code,
    name, description, credits, and number of sections. Use get_course_sections
    to get full section details for a specific course.

    Args:
        query: A course code (e.g. "COP3530", "COP") or course name
               (e.g. "Data Structures"). Spaces in codes are optional.
    """
    # Normalize: remove spaces so "COP 3530" becomes "COP3530" for code search
    query_normalized = query.strip().replace(" ", "")

    # Try code-based search first (exact then prefix)
    results = search_by_code(query_normalized, limit=10)

    # If no code matches, try name search with the original query
    if not results:
        results = search_by_name(query.strip(), limit=10)

    if not results:
        return f'No courses found matching "{query}". Try a different course code or name.'

    formatted = [_format_course_summary(c, i + 1) for i, c in enumerate(results)]
    header = f'Found {len(results)} course(s) matching "{query}":\n'
    return header + "\n\n".join(formatted)


@tool
def get_course_sections(course_code: str) -> str:
    """Get detailed section information for a specific UF course.

    Use this tool after search_courses to get full details about a course's
    sections, including instructors, schedule, meeting locations, delivery mode,
    gen-ed designations, and more.

    Args:
        course_code: The exact course code (e.g. "COP3530"). Spaces are
                     optional (e.g. "COP 3530" also works).
    """
    code_normalized = course_code.strip().replace(" ", "").upper()
    entries = get_courses(code_normalized)

    if not entries:
        return (
            f'No course found with code "{course_code}". '
            "Use search_courses to find the correct course code first."
        )

    from collections import Counter

    output_parts: list[str] = []

    for entry in entries:
        lines: list[str] = []

        # --- Course header ---
        code_spaced = entry.get("codeWithSpace", entry["code"])
        name = entry["name"]
        description = entry.get("description", "").strip()
        prerequisites = entry.get("prerequisites", "").strip()
        sections = entry.get("sections", [])

        lines.append(f"{code_spaced} - {name}")
        if description:
            lines.append(f"Description: {description}")
        if prerequisites:
            lines.append(f"{prerequisites}")

        # --- Aggregate stats ---
        total = len(sections)
        if sections:
            all_credits_min = [s.get("credits_min", s.get("credits", 0)) for s in sections]
            all_credits_max = [s.get("credits_max", s.get("credits", 0)) for s in sections]
            cmin = min(all_credits_min)
            cmax = max(all_credits_max)
            credit_str = str(cmin) if cmin == cmax else f"{cmin}-{cmax}"
        else:
            credit_str = "N/A"

        # Delivery mode breakdown
        mode_counts = Counter(
            _DELIVERY_MODE.get(s.get("sectWeb", ""), "Unknown") for s in sections
        )
        mode_summary = ", ".join(f"{count} {mode}" for mode, count in mode_counts.items())

        # Gen-ed and Quest tags (unique across all sections)
        all_gen_ed = sorted({tag for s in sections for tag in s.get("genEd", [])})
        all_quest = sorted({tag for s in sections for tag in s.get("quest", [])})

        lines.append(f"Total Sections: {total} | Credits: {credit_str}")
        lines.append(f"Delivery: {mode_summary}")
        if all_gen_ed:
            lines.append(f"Gen Ed: {', '.join(all_gen_ed)}")
        if all_quest:
            lines.append(f"Quest: {', '.join(all_quest)}")

        lines.append("")  # blank line before sections

        # --- Per-section details ---
        for section in sections:
            lines.append(_format_section(section))
            lines.append("")  # blank line between sections

        output_parts.append("\n".join(lines).rstrip())

    # If multiple entries share the same code, separate them clearly
    if len(output_parts) > 1:
        header = f"Found {len(output_parts)} course listing(s) under {code_normalized}:\n"
        separator = "\n" + "-" * 60 + "\n"
        return header + separator.join(output_parts)

    return output_parts[0]
