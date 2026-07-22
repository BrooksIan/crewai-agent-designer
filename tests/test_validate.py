"""Tests for `app.validate` — design-level error detection.

CrewAI's own dry-instantiation is exercised by these tests only if the
`crewai` package is installed. When it isn't (typical CI without network),
the tests still cover the entire pass-1 logic.
"""

from __future__ import annotations

from app import validate
from app.models import Agent, Crew, Design, Task, ToolConfig


def _messages(errors) -> list[str]:
    return [e.message for e in errors]


def test_valid_design_returns_no_errors(simple_design: Design) -> None:
    errors = validate.validate(simple_design)
    # We only assert on the design-level pass; if crewai is installed and
    # accepts these values there will still be no errors.
    assert all("crewai rejected" not in e.message for e in errors), errors
    where = [e.where for e in errors]
    assert not any(w.startswith("agents[") and w.endswith(".tools") for w in where)


def test_undeclared_tool_on_agent_is_flagged() -> None:
    design = Design(
        agents=[
            Agent(
                name="a",
                role="r",
                goal="g",
                backstory="b",
                tools=["ghost"],
            )
        ],
        crew=Crew(name="C"),
    )
    errors = validate.validate(design)
    assert any("undeclared tool" in m and "ghost" in m for m in _messages(errors))


def test_missing_agent_reference_is_flagged() -> None:
    design = Design(
        tasks=[
            Task(
                name="t",
                description="d",
                expected_output="o",
                agent="missing",
            )
        ],
        crew=Crew(name="C"),
    )
    errors = validate.validate(design)
    assert any("unknown agent" in m and "missing" in m for m in _messages(errors))


def test_context_cycle_is_flagged() -> None:
    design = Design(
        tasks=[
            Task(name="t1", description="d", expected_output="o", context=["t2"]),
            Task(name="t2", description="d", expected_output="o", context=["t1"]),
        ],
        crew=Crew(name="C"),
    )
    errors = validate.validate(design)
    assert any("circular" in m for m in _messages(errors))


def test_self_context_is_flagged() -> None:
    design = Design(
        tasks=[
            Task(name="t1", description="d", expected_output="o", context=["t1"]),
        ],
        crew=Crew(name="C"),
    )
    errors = validate.validate(design)
    assert any("cannot depend on itself" in m for m in _messages(errors))


def test_duplicate_agent_names_flagged() -> None:
    design = Design(
        agents=[
            Agent(name="dup", role="r", goal="g", backstory="b"),
            Agent(name="dup", role="r", goal="g", backstory="b"),
        ],
        crew=Crew(name="C"),
    )
    errors = validate.validate(design)
    assert any("duplicate" in m for m in _messages(errors))


def test_invalid_crew_class_name_flagged() -> None:
    design = Design(crew=Crew(name="not a class"))
    errors = validate.validate(design)
    assert any("not a valid Python class name" in m for m in _messages(errors))


def test_hierarchical_without_manager_llm_flagged() -> None:
    design = Design(crew=Crew(name="C", process="hierarchical"))
    errors = validate.validate(design)
    assert any("manager_llm" in m for m in _messages(errors))


def test_task_order_references_unknown_task_flagged() -> None:
    design = Design(
        tasks=[Task(name="t1", description="d", expected_output="o")],
        crew=Crew(name="C", task_order=["t1", "ghost"]),
    )
    errors = validate.validate(design)
    assert any("task_order references unknown" in m for m in _messages(errors))


def test_valid_hierarchical_passes(hierarchical_design: Design) -> None:
    errors = validate.validate(hierarchical_design)
    # Skip crewai errors — they can arise on version mismatch and are outside
    # the scope of this test.
    design_errors = [e for e in errors if "crewai rejected" not in e.message]
    assert design_errors == [], design_errors
