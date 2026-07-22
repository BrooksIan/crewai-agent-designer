"""Tests for `app.canvas_actions` — reference-safe delete operations.

Every delete call:

1. Removes the target entity.
2. Scrubs every stale reference elsewhere in the Design so the exporter
   doesn't error out immediately after.
3. Returns human-readable notes describing every cleanup — the Canvas
   tab surfaces these as ``st.warning``s so users understand what
   happened.

These are pure-function tests. The Streamlit tab wiring is exercised by
the streamlit-boot smoke test in the Phase 2 verification, not here.
"""

from __future__ import annotations

import pytest

from app import canvas_actions
from app.models import Agent, Crew, Design, Task, ToolConfig


# ---------------------------------------------------------------------------
# Fixtures — a "loaded" design that exercises every cross-reference type
# ---------------------------------------------------------------------------

@pytest.fixture
def wired_design() -> Design:
    """A design where every cross-reference is populated:

    - Two agents, both with tool bindings.
    - Three tasks: two assigned to agents, one depending on another via
      ``context``. Task tools are also set.
    - crew.task_order references two tasks (so we can watch it clean up).
    """
    return Design(
        agents=[
            Agent(name="a1", role="r", goal="g", backstory="b", tools=["tl1"]),
            Agent(name="a2", role="r", goal="g", backstory="b", tools=["tl1", "tl2"]),
        ],
        tasks=[
            Task(
                name="t1", description="d", expected_output="o",
                agent="a1", tools=["tl1"],
            ),
            Task(
                name="t2", description="d", expected_output="o",
                agent="a2", tools=["tl2"], context=["t1"],
            ),
            Task(name="t3", description="d", expected_output="o", agent="a1"),
        ],
        tools=[
            ToolConfig(name="tl1", kind="SerperDevTool"),
            ToolConfig(name="tl2", kind="WebsiteSearchTool", params={"website": "https://x"}),
        ],
        crew=Crew(name="C", task_order=["t1", "t2", "t3"]),
    )


# ---------------------------------------------------------------------------
# delete_agent
# ---------------------------------------------------------------------------

def test_delete_agent_removes_agent(wired_design: Design) -> None:
    canvas_actions.delete_agent(wired_design, "a1")
    assert [a.name for a in wired_design.agents] == ["a2"]


def test_delete_agent_unsets_task_agent(wired_design: Design) -> None:
    """Every task pinned to the deleted agent loses its `agent` field."""
    canvas_actions.delete_agent(wired_design, "a1")
    tasks_by_name = {t.name: t for t in wired_design.tasks}
    assert tasks_by_name["t1"].agent is None
    assert tasks_by_name["t3"].agent is None
    # t2 wasn't on a1 — untouched.
    assert tasks_by_name["t2"].agent == "a2"


def test_delete_agent_returns_warnings_for_orphaned_tasks(wired_design: Design) -> None:
    warnings = canvas_actions.delete_agent(wired_design, "a1")
    # t1 and t3 both lost their agent.
    assert len(warnings) == 2
    assert all("a1" in w for w in warnings)


def test_delete_missing_agent_is_a_noop(wired_design: Design) -> None:
    """A stale delete (agent already gone) must not raise or corrupt state."""
    before = wired_design.model_dump()
    warnings = canvas_actions.delete_agent(wired_design, "does_not_exist")
    assert warnings == []
    assert wired_design.model_dump() == before


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------

def test_delete_task_removes_task(wired_design: Design) -> None:
    canvas_actions.delete_task(wired_design, "t1")
    assert [t.name for t in wired_design.tasks] == ["t2", "t3"]


def test_delete_task_scrubs_from_other_tasks_context(wired_design: Design) -> None:
    """t2 depended on t1 in its context — after deletion, that entry is gone."""
    canvas_actions.delete_task(wired_design, "t1")
    t2 = next(t for t in wired_design.tasks if t.name == "t2")
    assert t2.context == []


def test_delete_task_scrubs_from_crew_task_order(wired_design: Design) -> None:
    canvas_actions.delete_task(wired_design, "t2")
    assert wired_design.crew.task_order == ["t1", "t3"]


def test_delete_task_returns_warnings(wired_design: Design) -> None:
    """The task deletion above should surface a context-cleanup and a
    task_order-cleanup warning."""
    warnings = canvas_actions.delete_task(wired_design, "t1")
    joined = "\n".join(warnings)
    assert "context" in joined  # t2's context dep on t1 was cleaned
    assert "task_order" in joined or "execution order" in joined  # crew.task_order was cleaned


def test_delete_missing_task_is_a_noop(wired_design: Design) -> None:
    before = wired_design.model_dump()
    warnings = canvas_actions.delete_task(wired_design, "not_a_task")
    assert warnings == []
    assert wired_design.model_dump() == before


# ---------------------------------------------------------------------------
# delete_tool
# ---------------------------------------------------------------------------

def test_delete_tool_removes_tool(wired_design: Design) -> None:
    canvas_actions.delete_tool(wired_design, "tl1")
    assert [t.name for t in wired_design.tools] == ["tl2"]


def test_delete_tool_scrubs_from_agent_tools(wired_design: Design) -> None:
    """Both agents used tl1 — both lose the binding, other tools kept."""
    canvas_actions.delete_tool(wired_design, "tl1")
    agents_by_name = {a.name: a for a in wired_design.agents}
    assert agents_by_name["a1"].tools == []
    assert agents_by_name["a2"].tools == ["tl2"]  # tl1 stripped, tl2 kept


def test_delete_tool_scrubs_from_task_tools(wired_design: Design) -> None:
    """Task-level tool overrides also get scrubbed."""
    canvas_actions.delete_tool(wired_design, "tl1")
    t1 = next(t for t in wired_design.tasks if t.name == "t1")
    assert t1.tools == []


def test_delete_tool_returns_warning_per_binding(wired_design: Design) -> None:
    """tl1 was bound by a1 (agent), a2 (agent), and t1 (task) — three warnings."""
    warnings = canvas_actions.delete_tool(wired_design, "tl1")
    # Two agents + one task = three cleanup notes.
    assert len(warnings) == 3
    assert sum(1 for w in warnings if "Agent" in w) == 2
    assert sum(1 for w in warnings if "Task" in w) == 1


def test_delete_missing_tool_is_a_noop(wired_design: Design) -> None:
    before = wired_design.model_dump()
    warnings = canvas_actions.delete_tool(wired_design, "not_a_tool")
    assert warnings == []
    assert wired_design.model_dump() == before


# ---------------------------------------------------------------------------
# Empty design edge case
# ---------------------------------------------------------------------------

def test_delete_from_empty_design_is_a_noop() -> None:
    """Design with nothing in it must survive any delete call unchanged."""
    d = Design()
    assert canvas_actions.delete_agent(d, "x") == []
    assert canvas_actions.delete_task(d, "x") == []
    assert canvas_actions.delete_tool(d, "x") == []
    assert d.model_dump() == Design().model_dump()


# ---------------------------------------------------------------------------
# End-to-end: build a design via canvas actions, verify final state
# ---------------------------------------------------------------------------

def test_end_to_end_delete_chain_leaves_valid_design() -> None:
    """Simulate a user deleting each of the three kinds in turn. The
    remaining design must still parse cleanly and every cross-ref must
    resolve."""
    d = Design(
        agents=[
            Agent(name="researcher", role="r", goal="g", backstory="b", tools=["ws"]),
            Agent(name="writer", role="r", goal="g", backstory="b"),
        ],
        tasks=[
            Task(name="r_task", description="d", expected_output="o",
                 agent="researcher", tools=["ws"]),
            Task(name="w_task", description="d", expected_output="o",
                 agent="writer", context=["r_task"]),
        ],
        tools=[ToolConfig(name="ws", kind="SerperDevTool")],
    )

    # Delete the tool — both agent and task bindings should clean up.
    canvas_actions.delete_tool(d, "ws")
    assert d.tools == []
    assert next(a for a in d.agents if a.name == "researcher").tools == []
    assert next(t for t in d.tasks if t.name == "r_task").tools == []

    # Delete the researcher — its task should lose its agent.
    canvas_actions.delete_agent(d, "researcher")
    assert [a.name for a in d.agents] == ["writer"]
    assert next(t for t in d.tasks if t.name == "r_task").agent is None

    # Delete the first task — the second's context should shrink.
    canvas_actions.delete_task(d, "r_task")
    assert [t.name for t in d.tasks] == ["w_task"]
    assert next(t for t in d.tasks if t.name == "w_task").context == []


def test_clear_design_wipes_entities_preserves_workplace(wired_design: Design) -> None:
    wired_design.workplace = "TeamA"
    canvas_actions.clear_design(wired_design)
    assert wired_design.agents == []
    assert wired_design.tasks == []
    assert wired_design.tools == []
    assert wired_design.crew.name == "MyCrew"
    assert wired_design.crew.process == "sequential"
    assert wired_design.workplace == "TeamA"


def test_clear_design_on_empty_is_noop() -> None:
    d = Design(workplace="W")
    canvas_actions.clear_design(d)
    assert d.agents == []
    assert d.workplace == "W"