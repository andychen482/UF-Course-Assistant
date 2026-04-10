"""
Pytest configuration and shared fixtures for the UF Course Assistant LLM tests.

These tests verify that the LLM picks the correct tool(s) for a given student
prompt. Real tools are stubbed via monkeypatch so no network / database / file
access happens during the run -- only the LLM call itself is real.

How it works
------------
1. Every @tool object exposes a ``.func`` attribute (the wrapped callable).
   We replace that callable with a stub that records the call into a list and
   returns a canned "success" string. The LangChain agent still sees the same
   tool *names*, *descriptions*, and *schemas*, so the LLM's tool-routing
   behaviour is unchanged.
2. The agent fixture builds a fresh agent (same config as production) per
   test using ``chat.SYSTEM_PROMPT`` and the stubbed tools.
3. Tests send a realistic student prompt, then walk ``response["messages"]``
   to extract every ``AIMessage.tool_calls`` and assert on the names / args.
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Ensure the project root is importable and required env vars are set
# BEFORE any project module gets imported (utils.constants raises at import
# time if ALLOWED_ORIGINS is missing).
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault(
    "ALLOWED_ORIGINS", "http://localhost:3000,https://ufscheduler.com"
)

import pytest  # noqa: E402

# Project imports (must come after sys.path / env setup)
from chat import SYSTEM_PROMPT  # noqa: E402
from utils.constants import OPENAI_API_KEY  # noqa: E402

from langchain_openai import ChatOpenAI  # noqa: E402
from langchain.agents import create_agent  # noqa: E402

from tools.course_search import (  # noqa: E402
    search_courses_by_code,
    search_courses_by_title,
    get_course_sections,
)
from tools.rmp_search import (  # noqa: E402
    search_professor_rating,
    get_professor_reviews,
)
from tools.reddit_search import search_reddit  # noqa: E402
from tools.gatorevals_search import (  # noqa: E402
    search_gatorevals,
    search_gatorevals_course,
)
from tools.scheduler_actions import (  # noqa: E402
    add_course_to_scheduler,
    switch_scheduler_view,
    remove_course_from_scheduler,
)


# ---------------------------------------------------------------------------
# Tool registry -- single source of truth for stub installation and agent build
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    search_courses_by_code,
    search_courses_by_title,
    get_course_sections,
    search_professor_rating,
    get_professor_reviews,
    search_reddit,
    search_gatorevals,
    search_gatorevals_course,
    add_course_to_scheduler,
    switch_scheduler_view,
    remove_course_from_scheduler,
]


# ---------------------------------------------------------------------------
# Stub installation
# ---------------------------------------------------------------------------

def _make_stub(tool_name: str, recorder: list[dict]):
    """Build a no-op stub that records the call and returns a canned reply."""

    def _stub(*args, **kwargs):
        # Combine positional and keyword args. Most LangChain tools are
        # invoked with keyword args, but be defensive.
        call_args = dict(kwargs)
        if args:
            call_args.setdefault("_positional", list(args))
        recorder.append({"name": tool_name, "args": call_args})
        # Return something that *looks* like a successful tool result so the
        # LLM doesn't try to "retry" with a different tool.
        return (
            f"[TEST STUB] {tool_name} returned successfully. "
            "Pretend the data was retrieved and proceed."
        )

    return _stub


def _install_stub(tool_obj, recorder: list[dict]) -> None:
    """Replace ``tool_obj.func`` with a recording stub.

    Pydantic v2 models normally allow attribute assignment, but we fall back
    to ``object.__setattr__`` if validation rejects the assignment.
    """
    stub = _make_stub(tool_obj.name, recorder)
    try:
        tool_obj.func = stub
    except Exception:
        object.__setattr__(tool_obj, "func", stub)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tool_recorder(request):
    """Patch every tool's ``.func`` with a stub and yield the recorded-call list.

    The list grows as the LLM calls tools during the test. A finalizer
    restores the original functions after the test, even if assertions fail.
    """
    recorder: list[dict] = []
    originals: list[tuple] = []

    for tool_obj in ALL_TOOLS:
        originals.append((tool_obj, tool_obj.func))
        # ``object.__setattr__`` reliably bypasses any Pydantic validation
        # on the StructuredTool model.
        object.__setattr__(tool_obj, "func", _make_stub(tool_obj.name, recorder))

    def _restore():
        for tool_obj, original in originals:
            object.__setattr__(tool_obj, "func", original)

    request.addfinalizer(_restore)
    return recorder


@pytest.fixture
def agent(tool_recorder):
    """Build a fresh agent (same config as ``chat.build_agent``) with stubbed tools."""
    if not OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY is not set; skipping LLM tool-call tests.")

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=OPENAI_API_KEY,
        temperature=0,  # deterministic-ish for stable assertions
    )
    return create_agent(model=llm, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)
