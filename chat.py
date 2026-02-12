"""
UF Course Assistant -- interactive chatbot with course search tools.

Usage:
    python chat.py
"""

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

from tools.course_search import search_courses, get_course_sections
from tools.rmp_search import search_professor_rating

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()  # loads .env into os.environ

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
- Be concise but thorough. Students are busy -- get to the point.\
"""

# ---------------------------------------------------------------------------
# Agent setup
# ---------------------------------------------------------------------------

def build_agent():
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        api_key=os.environ.get("OPENAI_API_KEY"),
    )

    tools = [search_courses, get_course_sections, search_professor_rating]

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )

    return agent


# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  UF Course Assistant (Spring 2026)")
    print("  Type your question, or 'quit' to exit.")
    print("=" * 60)
    print()

    agent = build_agent()
    conversation_history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        conversation_history.append({"role": "user", "content": user_input})

        response = agent.invoke({"messages": conversation_history})

        # The last message in the response is the assistant's final answer
        assistant_message = response["messages"][-1]
        assistant_text = assistant_message.content

        conversation_history.append({"role": "assistant", "content": assistant_text})

        print(f"\nAssistant: {assistant_text}\n")


if __name__ == "__main__":
    main()
