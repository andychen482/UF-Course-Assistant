"""
LLM tool-call tests for the UF Course Assistant.

Each test sends a realistic student prompt to the agent and verifies that the
LLM picked the right tool(s). Tools are stubbed in conftest.py so no real
network / database / file access happens -- only the LLM call itself is real.

═══════════════════════════════════════════════════════════════════════════
RUNNING THE TESTS
═══════════════════════════════════════════════════════════════════════════
  # Run the entire suite
  pytest tests/test_llm_tool_calls.py

  # Verbose -- show each test name and PASS/FAIL status as it runs
  pytest tests/test_llm_tool_calls.py -v

  # Even more verbose -- show full assertion diffs and captured stdout
  pytest tests/test_llm_tool_calls.py -vv -s

  # Run a single class (one test "suite")
  pytest tests/test_llm_tool_calls.py::TestRateMyProfessors -v

  # Run a single test method
  pytest tests/test_llm_tool_calls.py::TestSchedulerActions::test_add_course -v

  # Run a single parametrized case
  pytest "tests/test_llm_tool_calls.py::TestScope::test_off_topic_prompts_make_no_tool_calls[What's the capital of France?]"

  # Run every test whose name matches a substring (works across classes)
  pytest tests/test_llm_tool_calls.py -k "compare" -v
  pytest tests/test_llm_tool_calls.py -k "gatorevals and not course" -v

  # Stop on first failure (useful when iterating on a flaky case)
  pytest tests/test_llm_tool_calls.py -x

  # Re-run only the tests that failed last time
  pytest tests/test_llm_tool_calls.py --lf

  # Run N tests in parallel (requires `pip install pytest-xdist`)
  pytest tests/test_llm_tool_calls.py -n 4

Common flag cheat-sheet:
  -v / -vv     more verbose output (test names, then full diffs)
  -s           don't capture stdout (so prints show up live)
  -x           exit on first failure
  -k EXPR      filter tests by name substring / boolean expression
  --lf         only re-run last-failed tests
  --collect-only   list tests without running them

═══════════════════════════════════════════════════════════════════════════
HOW TO ADD A NEW TEST
═══════════════════════════════════════════════════════════════════════════
Append a new method to the most relevant ``Test*`` class (or create a new
class). A minimal test looks like:

    def test_my_case(self, agent, tool_recorder):
        run_prompt(agent, "Some realistic student question.")
        names = tool_names(tool_recorder)
        assert "expected_tool" in names

Fixtures available (auto-injected by pytest from conftest.py):
  - ``agent``         fresh LangChain agent with stubbed tools
  - ``tool_recorder`` running list of {"name", "args"} for every tool the
                     LLM called during this test, in order

Helpers (defined below in this file):
  - ``run_prompt(agent, prompt)``   send a single user prompt
  - ``tool_names(recorder)``        list[str] of tool names called
  - ``args_for(recorder, "name")``  list[dict] of args for that tool

For multi-call assertions (e.g. "compares two professors"), use
``names.count("search_professor_rating") >= 2``. For argument checks, use
``args_for(...)`` and inspect the dicts.

To parametrize a test across many prompts, use the standard
``@pytest.mark.parametrize`` decorator -- see ``TestSchedulerActions`` and
``TestScope`` below for examples.

═══════════════════════════════════════════════════════════════════════════
NOTES ON STABILITY
═══════════════════════════════════════════════════════════════════════════
LLM outputs are not perfectly deterministic even at temperature=0. Tests
assert on the *minimum* expected behaviour ("this tool was called at least
once") rather than exact call counts where the LLM has flexibility.
"""

from __future__ import annotations

import pytest

from utils.constants import INTRO_MESSAGE


# ───────────────────────────────────────────────────────────────────────────
# Helpers (kept inline so the test file has zero cross-file imports
# beyond fixtures, which pytest auto-injects from conftest.py)
# ───────────────────────────────────────────────────────────────────────────

def run_prompt(agent, prompt: str) -> dict:
    """Send a single user prompt (after the standard intro) and return the response."""
    history = [
        {"role": "assistant", "content": INTRO_MESSAGE},
        {"role": "user", "content": prompt},
    ]
    return agent.invoke({"messages": history})


def tool_names(recorder: list[dict]) -> list[str]:
    """Return the ordered list of tool names the LLM called."""
    return [c["name"] for c in recorder]


def args_for(recorder: list[dict], tool_name: str) -> list[dict]:
    """Return the args dicts for every call to ``tool_name``."""
    return [c["args"] for c in recorder if c["name"] == tool_name]


# ───────────────────────────────────────────────────────────────────────────
# Course search & section lookup
# ───────────────────────────────────────────────────────────────────────────

class TestCourseSearch:
    def test_search_by_explicit_code(self, agent, tool_recorder):
        run_prompt(agent, "What's COP3530 about?")
        names = tool_names(tool_recorder)
        assert "search_courses_by_code" in names
        # The LLM should pass the actual code through
        code_args = args_for(tool_recorder, "search_courses_by_code")
        assert any("COP3530" in (a.get("course_code") or "").upper().replace(" ", "")
                   for a in code_args)

    def test_search_by_topic_keyword(self, agent, tool_recorder):
        run_prompt(agent, "Are there any classes that cover machine learning?")
        names = tool_names(tool_recorder)
        assert "search_courses_by_title" in names

    def test_section_details_request(self, agent, tool_recorder):
        run_prompt(
            agent,
            "Who's teaching COP3530 this semester and when does it meet?",
        )
        names = tool_names(tool_recorder)
        assert "get_course_sections" in names

    def test_compare_two_courses_calls_sections_for_both(self, agent, tool_recorder):
        """Comparing schedules requires section data for BOTH courses."""
        run_prompt(
            agent,
            "Compare the meeting times for COP3530 and COP4600 -- I need to "
            "decide which one fits my schedule.",
        )
        names = tool_names(tool_recorder)
        section_calls = args_for(tool_recorder, "get_course_sections")
        # Either two separate get_course_sections calls, or two course-search
        # calls then sections -- but section data must be fetched for both.
        codes_seen = {
            (a.get("course_code") or "").upper().replace(" ", "")
            for a in section_calls
        }
        assert "COP3530" in codes_seen and "COP4600" in codes_seen


# ───────────────────────────────────────────────────────────────────────────
# RateMyProfessors
# ───────────────────────────────────────────────────────────────────────────

class TestRateMyProfessors:
    def test_basic_rating_lookup(self, agent, tool_recorder):
        run_prompt(agent, "How is Amanpreet Kapoor rated on RateMyProfessors?")
        names = tool_names(tool_recorder)
        assert "search_professor_rating" in names
        # Per the system prompt: a pure RMP question must NOT also fetch GatorEvals
        assert "search_gatorevals" not in names

    def test_recent_reviews_request(self, agent, tool_recorder):
        run_prompt(
            agent,
            "Pull the most recent RMP reviews for Amanpreet Kapoor -- I want "
            "to see what current students are saying.",
        )
        names = tool_names(tool_recorder)
        assert "get_professor_reviews" in names

    def test_compare_two_professors_on_rmp(self, agent, tool_recorder):
        """Comparing two profs should hit RMP twice (once per prof)."""
        run_prompt(
            agent,
            "On RateMyProfessors, who has better ratings -- Amanpreet Kapoor "
            "or Christina Gardner-McCune?",
        )
        names = tool_names(tool_recorder)
        assert names.count("search_professor_rating") >= 2


# ───────────────────────────────────────────────────────────────────────────
# Reddit
# ───────────────────────────────────────────────────────────────────────────

class TestReddit:
    def test_student_opinions_for_course(self, agent, tool_recorder):
        run_prompt(agent, "What are students on Reddit saying about COP3530?")
        names = tool_names(tool_recorder)
        assert "search_reddit" in names

    def test_major_workload_question(self, agent, tool_recorder):
        run_prompt(
            agent,
            "Anything on r/UFL about how heavy the CS major workload is?",
        )
        names = tool_names(tool_recorder)
        assert "search_reddit" in names

    def test_general_advice_topic(self, agent, tool_recorder):
        run_prompt(
            agent,
            "What does Reddit say about registration tips for sophomores?",
        )
        names = tool_names(tool_recorder)
        assert "search_reddit" in names


# ───────────────────────────────────────────────────────────────────────────
# GatorEvals (instructor + course + comparison)
# ───────────────────────────────────────────────────────────────────────────

class TestGatorEvals:
    def test_instructor_evaluations_only(self, agent, tool_recorder):
        run_prompt(
            agent,
            "What are Amanpreet Kapoor's GatorEvals scores?",
        )
        names = tool_names(tool_recorder)
        assert "search_gatorevals" in names
        # System prompt forbids piggy-backing RMP on a GatorEvals question
        assert "search_professor_rating" not in names
        assert "get_professor_reviews" not in names

    def test_course_evaluations_by_code(self, agent, tool_recorder):
        run_prompt(agent, "How are the GatorEvals for COP3530 overall?")
        names = tool_names(tool_recorder)
        assert "search_gatorevals_course" in names

    def test_course_evaluations_by_name(self, agent, tool_recorder):
        run_prompt(
            agent, "What do the GatorEvals look like for Data Structures?"
        )
        names = tool_names(tool_recorder)
        assert "search_gatorevals_course" in names

    def test_instructor_vs_course_comparison(self, agent, tool_recorder):
        """Best path: ONE call to search_gatorevals_course with both query
        and instructor_name (per the system prompt's guidance)."""
        run_prompt(
            agent,
            "Does Amanpreet Kapoor score above or below the COP3530 "
            "GatorEvals average?",
        )
        names = tool_names(tool_recorder)
        assert "search_gatorevals_course" in names
        course_calls = args_for(tool_recorder, "search_gatorevals_course")
        # At least one of those calls should pass an instructor_name so the
        # tool returns a side-by-side comparison.
        assert any(c.get("instructor_name") for c in course_calls)

    def test_compare_two_instructors(self, agent, tool_recorder):
        """Comparing two instructors -> two instructor lookups."""
        run_prompt(
            agent,
            "Compare GatorEvals between Amanpreet Kapoor and "
            "Christina Gardner-McCune -- who's the better instructor?",
        )
        names = tool_names(tool_recorder)
        assert names.count("search_gatorevals") >= 2


# ───────────────────────────────────────────────────────────────────────────
# Scheduler action tools
# ───────────────────────────────────────────────────────────────────────────

class TestSchedulerActions:
    def test_add_course(self, agent, tool_recorder):
        run_prompt(agent, "Add COP3530 to my schedule please.")
        names = tool_names(tool_recorder)
        assert "add_course_to_scheduler" in names
        add_args = args_for(tool_recorder, "add_course_to_scheduler")
        assert any(
            "COP3530" in (a.get("course_code") or "").upper().replace(" ", "")
            for a in add_args
        )

    def test_remove_course(self, agent, tool_recorder):
        run_prompt(agent, "Take COP3530 off my schedule.")
        names = tool_names(tool_recorder)
        assert "remove_course_from_scheduler" in names
        rm_args = args_for(tool_recorder, "remove_course_from_scheduler")
        assert any(
            "COP3530" in (a.get("course_code") or "").upper().replace(" ", "")
            for a in rm_args
        )

    @pytest.mark.parametrize(
        "phrase, expected_view",
        [
            ("Show me the graph view.", "graph"),
            ("Switch to the calendar view.", "calendar"),
            ("Open the campus map.", "map"),
            ("switch my schedule view to be on the campus map.", "direct map"),
            ("Pull up my multi-semester plan view.", "plan"),
            ("switch my schedule view to pull up my multi-semester plan view.", "direct plan"),
        ],
    )
    def test_switch_view_routes_correctly(
        self, agent, tool_recorder, phrase, expected_view
    ):
        run_prompt(agent, phrase)
        names = tool_names(tool_recorder)
        assert "switch_scheduler_view" in names
        view_args = args_for(tool_recorder, "switch_scheduler_view")
        seen_views = {(a.get("view") or "").lower() for a in view_args}
        assert expected_view in seen_views


# ───────────────────────────────────────────────────────────────────────────
# Multi-tool / cross-source prompts
# ───────────────────────────────────────────────────────────────────────────

class TestMultiTool:
    def test_explicit_rmp_and_gatorevals(self, agent, tool_recorder):
        """When the student explicitly asks for BOTH sources, both must fire."""
        run_prompt(
            agent,
            "Pull both Amanpreet Kapoor's RateMyProfessors rating AND his "
            "GatorEvals scores.",
        )
        names = tool_names(tool_recorder)
        assert "search_professor_rating" in names
        assert "search_gatorevals" in names

    def test_course_lookup_plus_reddit_opinions(self, agent, tool_recorder):
        """Combined info-and-opinion question -> course tool + reddit tool."""
        run_prompt(
            agent,
            "Tell me what COP3530 is about, and what redditors think of it.",
        )
        names = tool_names(tool_recorder)
        assert "search_reddit" in names
        assert any(
            n in names for n in ("search_courses_by_code", "get_course_sections")
        )

    def test_full_decision_help_uses_multiple_sources(self, agent, tool_recorder):
        """Realistic 'help me decide' prompt naming three sources."""
        run_prompt(
            agent,
            "I'm trying to decide whether to take COP3530 with Amanpreet "
            "Kapoor next semester. Show me the section info, his "
            "RateMyProfessors rating, and what Reddit says.",
        )
        names = tool_names(tool_recorder)
        assert "search_professor_rating" in names
        assert "search_reddit" in names
        assert any(
            n in names for n in ("search_courses_by_code", "get_course_sections")
        )

    def test_compare_two_professors_for_same_course(self, agent, tool_recorder):
        """Two professors, one course -> two RMP lookups (and ideally section data)."""
        run_prompt(
            agent,
            "I'm picking between Amanpreet Kapoor and Christina Gardner-McCune "
            "for COP3530. Compare their RateMyProfessors ratings.",
        )
        names = tool_names(tool_recorder)
        assert names.count("search_professor_rating") >= 2


# ───────────────────────────────────────────────────────────────────────────
# Scope guard -- the assistant must not call tools for off-topic questions
# ───────────────────────────────────────────────────────────────────────────

class TestScope:
    @pytest.mark.parametrize(
        "off_topic_prompt",
        [
            "Can you give me a recipe for chocolate chip cookies?",
            "What's the capital of France?",
            "Help me debug this Python script: print('hello'",
            "Who won the Super Bowl last year?",
        ],
    )
    def test_off_topic_prompts_make_no_tool_calls(
        self, agent, tool_recorder, off_topic_prompt
    ):
        run_prompt(agent, off_topic_prompt)
        # No tool should fire for an out-of-scope question.
        assert tool_recorder == [], (
            f"Expected zero tool calls for off-topic prompt, "
            f"got: {tool_names(tool_recorder)}"
        )
