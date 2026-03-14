"""
Client-side action tools for the scheduler UI.

These tools propose actions in the student's scheduler interface. They execute
server-side but only return a "proposed" status string -- the actual UI mutation
happens on the frontend after the student confirms via a confirmation card.
"""

from __future__ import annotations

from langchain_core.tools import tool

CLIENT_ACTION_TOOLS = frozenset({
    "add_course_to_scheduler",
    "switch_scheduler_view",
    "remove_course_from_scheduler",
})


@tool
def add_course_to_scheduler(course_code: str) -> str:
    """Add a course to the student's selected courses in the scheduler.
    Args:
        course_code: The exact course code to add (e.g. "COP3530").
    """
    return f'Proposed action: add "{course_code}" to selected courses. Awaiting student confirmation.'


@tool
def switch_scheduler_view(view: str) -> str:
    """Switch the scheduler's main view panel.
    Args:
        view: One of "calendar", "graph", "map", or "plan".
    """
    return f'Proposed action: switch view to "{view}". Awaiting student confirmation.'


@tool
def remove_course_from_scheduler(course_code: str) -> str:
    """Remove a course from the student's selected courses list.
    Args:
        course_code: The course code to remove (e.g. "COP3530").
    """
    return f'Proposed action: remove "{course_code}" from selected courses. Awaiting student confirmation.'
