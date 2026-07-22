"""pytest fixtures shared across the test suite.

Adds the project root to ``sys.path`` so ``from app...`` works when pytest is
invoked from the project root.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from app.models import Agent, Crew, Design, Task, ToolConfig  # noqa: E402


@pytest.fixture
def simple_design() -> Design:
    """A minimal two-agent, two-task sequential crew with one tool."""
    return Design(
        agents=[
            Agent(
                name="researcher",
                role="Senior Researcher",
                goal="Find and summarize recent developments on {topic}.",
                backstory=(
                    "A veteran researcher with a knack for cutting through noise. "
                    "Reads sources deeply and cites everything."
                ),
                tools=["web_search"],
            ),
            Agent(
                name="writer",
                role="Technical Writer",
                goal="Turn research notes into a crisp brief on {topic}.",
                backstory="A former newsroom editor. Fewer adjectives, more nouns.",
                verbose=True,
            ),
        ],
        tasks=[
            Task(
                name="research",
                description="Investigate {topic} using the tools available.",
                expected_output="A markdown list of 5–10 findings with sources.",
                agent="researcher",
                tools=["web_search"],
            ),
            Task(
                name="write_brief",
                description="Turn the research notes into a two-paragraph brief.",
                expected_output="Two paragraphs. No headings. Max 300 words.",
                agent="writer",
                context=["research"],
            ),
        ],
        tools=[ToolConfig(name="web_search", kind="SerperDevTool")],
        crew=Crew(name="ResearchCrew", process="sequential", verbose=True),
    )


@pytest.fixture
def hierarchical_design() -> Design:
    """A minimal hierarchical crew — requires a manager_llm."""
    return Design(
        agents=[
            Agent(
                name="worker",
                role="Analyst",
                goal="Analyze what the manager delegates.",
                backstory="Steady, precise, uncomplaining.",
            ),
        ],
        tasks=[
            Task(
                name="analyze",
                description="Analyze the incoming payload.",
                expected_output="A JSON summary.",
                agent="worker",
            ),
        ],
        tools=[],
        crew=Crew(
            name="HierCrew",
            process="hierarchical",
            manager_llm="gpt-4o",
        ),
    )
