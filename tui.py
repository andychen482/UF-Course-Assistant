"""
UF Course Assistant -- Textual TUI for demo purposes.

Usage:
    python tui.py
"""

import os
from threading import Thread

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static

from tools.course_search import search_courses, get_course_sections
from tools.rmp_search import search_professor_rating

load_dotenv()

# ---------------------------------------------------------------------------
# System prompt (same as chat.py)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a friendly and knowledgeable course assistant for University of Florida \
(UF) students. Your job is to help students explore the UF course catalog for \
the current semester (Spring 2026).

You have access to three tools:
1. **search_courses** -- search for courses by course code (e.g. "COP3530", \
"COP") or by name (e.g. "Data Structures", "Calculus"). Use this first to \
find relevant courses.
2. **get_course_sections** -- get full section details for a specific course \
code, including instructors, schedules, locations, delivery mode, and more. \
Use this after identifying the right course from a search.
3. **search_professor_rating** -- look up a professor's rating, difficulty, \
and reviews on RateMyProfessors. Use the professor's full name as it appears \
in course section data (e.g. "Amanpreet Kapoor").

Guidelines:
- When a student asks about a course, search for it first, then retrieve \
section details if they need specifics like times, instructors, or locations.
- If a student asks about a professor's rating or reputation, use the \
search_professor_rating tool with the professor's full name.
- When a student is deciding between sections, you can proactively look up \
professor ratings to help them choose.
- Present information clearly and concisely. Summarize key details rather \
than dumping raw data.
- If a course code has multiple listings (e.g. Special Topics with different \
subtitles), mention all of them so the student can pick the right one.
- Help students compare sections when they ask about scheduling conflicts or \
choosing between sections.
- You can explain UF-specific terms: "periods" are UF's class time slots, \
gen-ed requirements, Quest designations, etc.
- Format your response in markdown for readability.
- Be concise but thorough. Students are busy -- get to the point.\
"""

# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

CSS = """
Screen {
    background: $surface;
}

#chat-view {
    height: 1fr;
    padding: 1 2;
    background: $surface;
}

.user-msg {
    background: #0021A5;
    color: #ffffff;
    padding: 1 2;
    margin: 1 0 0 12;
    border: round #0021A5;
}

.assistant-msg {
    background: $surface-darken-1;
    color: $text;
    padding: 1 2;
    margin: 1 12 0 0;
    border: round $primary-lighten-2;
}

.thinking-msg {
    background: $surface-darken-1;
    color: $text-muted;
    padding: 1 2;
    margin: 1 12 0 0;
    border: round $accent;
    text-style: italic;
}

.msg-label {
    color: $text-muted;
    text-style: bold;
    margin: 1 0 0 0;
    padding: 0 2;
}

#input-bar {
    dock: bottom;
    padding: 1 2;
    background: $surface;
}

#user-input {
    border: round $primary;
}

#user-input:focus {
    border: round $accent;
}

Header {
    background: #FA4616;
    color: #ffffff;
}
"""

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class UFCourseAssistant(App):
    """A TUI chatbot for UF course advising."""

    TITLE = "UF Course Assistant"
    SUB_TITLE = "Spring 2026"
    CSS = CSS

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.conversation_history: list[dict] = []
        self.agent = None
        self._thinking_widget = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="chat-view"):
            yield Static(
                "Welcome! Ask me anything about UF courses, sections, "
                "schedules, or professor ratings.",
                classes="assistant-msg",
            )
        yield Input(
            placeholder="Ask about courses, sections, or professors...",
            id="user-input",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#user-input", Input).focus()
        self._build_agent()

    @work(thread=True)
    def _build_agent(self) -> None:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
        tools = [search_courses, get_course_sections, search_professor_rating]
        self.agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
        )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_text = event.value.strip()
        if not user_text:
            return

        input_widget = self.query_one("#user-input", Input)
        input_widget.value = ""

        chat = self.query_one("#chat-view", VerticalScroll)

        # User bubble
        user_label = Static("You", classes="msg-label")
        user_bubble = Static(user_text, classes="user-msg")
        await chat.mount(user_label)
        await chat.mount(user_bubble)

        # Thinking indicator
        self._thinking_widget = Static("Thinking...", classes="thinking-msg")
        await chat.mount(self._thinking_widget)
        chat.scroll_end(animate=False)

        input_widget.disabled = True

        self.conversation_history.append({"role": "user", "content": user_text})
        self._get_response(user_text)

    @work(thread=True)
    def _get_response(self, user_text: str) -> None:
        if self.agent is None:
            self.app.call_from_thread(self._show_response, "Agent is still loading, please wait a moment...")
            return

        try:
            response = self.agent.invoke({"messages": self.conversation_history})
            assistant_message = response["messages"][-1]
            assistant_text = assistant_message.content
        except Exception as e:
            assistant_text = f"Error: {e}"

        self.conversation_history.append({"role": "assistant", "content": assistant_text})
        self.app.call_from_thread(self._show_response, assistant_text)

    def _show_response(self, text: str) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)

        # Remove thinking indicator
        if self._thinking_widget is not None:
            self._thinking_widget.remove()
            self._thinking_widget = None

        # Assistant bubble
        label = Static("Assistant", classes="msg-label")
        bubble = Markdown(text, classes="assistant-msg")
        chat.mount(label)
        chat.mount(bubble)
        chat.scroll_end(animate=False)

        self.query_one("#user-input", Input).disabled = False
        self.query_one("#user-input", Input).focus()


if __name__ == "__main__":
    app = UFCourseAssistant()
    app.run()
