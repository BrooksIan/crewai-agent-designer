"""Tests for `app.graph` — Design → canvas nodes/edges projection.

Focus areas:

- **Deterministic:** two calls with the same design return equal graphs
  (same node IDs, same edges, same order). Users get stable canvas
  layouts and diff-friendly test snapshots.
- **Edge coverage:** every `Task.agent`, `Task.context`, and
  `Agent.tools` reference that resolves to a real node produces an
  edge; unresolved references are silently skipped (validator flags
  them elsewhere).
- **Hierarchical crews:** a synthetic ``manager`` node appears and is
  wired to every non-manager agent.
- **Empty design:** no nodes, no edges — the Canvas tab uses this to
  show its empty state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import graph
from app.graph import Edge, Node
from app.models import Agent, Crew, Design, Task, ToolConfig


# ---------------------------------------------------------------------------
# Empty / trivial designs
# ---------------------------------------------------------------------------

def test_empty_design_produces_empty_graph() -> None:
    nodes, edges = graph.design_to_graph(Design())
    assert nodes == []
    assert edges == []


def test_single_agent_no_tasks_yields_one_node_no_edges() -> None:
    d = Design(agents=[Agent(name="solo", role="r", goal="g", backstory="b")])
    nodes, edges = graph.design_to_graph(d)
    assert [n.kind for n in nodes] == ["agent"]
    assert nodes[0].id == "agent:solo"
    assert nodes[0].label == "solo"
    assert edges == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_projection_is_deterministic(simple_design: Design) -> None:
    """Two calls must yield identical graphs — the canvas layout engine
    relies on stable IDs to preserve node positions across reruns."""
    a_nodes, a_edges = graph.design_to_graph(simple_design)
    b_nodes, b_edges = graph.design_to_graph(simple_design)
    assert a_nodes == b_nodes
    assert a_edges == b_edges


def test_node_ids_are_unique_across_kinds() -> None:
    """An agent named 'search' and a tool named 'search' must not collide."""
    d = Design(
        agents=[Agent(name="search", role="r", goal="g", backstory="b")],
        tools=[ToolConfig(name="search", kind="SerperDevTool")],
    )
    nodes, _ = graph.design_to_graph(d)
    ids = [n.id for n in nodes]
    assert len(ids) == len(set(ids)), "duplicate node IDs across kinds"
    assert "agent:search" in ids
    assert "tool:search" in ids


# ---------------------------------------------------------------------------
# Edge derivation
# ---------------------------------------------------------------------------

def test_agent_task_edges_from_task_agent_field(simple_design: Design) -> None:
    """Every Task with a resolvable agent produces one agent→task edge."""
    _, edges = graph.design_to_graph(simple_design)
    agent_task_edges = [e for e in edges if e.kind == "agent_task"]
    # simple_design fixture has 2 tasks each with an assigned agent
    assert len(agent_task_edges) == 2
    assert Edge(
        id="e:agent_task:researcher->research",
        source="agent:researcher",
        target="task:research",
        kind="agent_task",
    ) in agent_task_edges


def test_task_context_edges(simple_design: Design) -> None:
    """`Task.context = ['research']` produces a task→task edge into
    write_brief."""
    _, edges = graph.design_to_graph(simple_design)
    ctx_edges = [e for e in edges if e.kind == "task_context"]
    assert Edge(
        id="e:task_context:research->write_brief",
        source="task:research",
        target="task:write_brief",
        kind="task_context",
    ) in ctx_edges


def test_agent_tool_edges(simple_design: Design) -> None:
    """`Agent.tools = ['web_search']` produces a tool→agent edge."""
    _, edges = graph.design_to_graph(simple_design)
    tool_edges = [e for e in edges if e.kind == "agent_tool"]
    assert Edge(
        id="e:agent_tool:web_search->researcher",
        source="tool:web_search",
        target="agent:researcher",
        kind="agent_tool",
    ) in tool_edges


def test_unresolved_agent_ref_is_skipped_not_error() -> None:
    """A task pointing at a nonexistent agent must not blow up the
    projection. The validator flags it elsewhere."""
    d = Design(
        tasks=[Task(name="orphan", description="d", expected_output="o", agent="ghost")],
    )
    nodes, edges = graph.design_to_graph(d)
    assert [n.id for n in nodes] == ["task:orphan"]
    # No edge — 'ghost' has no matching node.
    assert edges == []


def test_unresolved_task_context_is_skipped() -> None:
    d = Design(
        tasks=[Task(name="t1", description="d", expected_output="o", context=["missing"])],
    )
    _, edges = graph.design_to_graph(d)
    assert edges == []


def test_unresolved_tool_ref_is_skipped() -> None:
    d = Design(
        agents=[Agent(name="a", role="r", goal="g", backstory="b", tools=["ghost_tool"])],
    )
    _, edges = graph.design_to_graph(d)
    assert edges == []


# ---------------------------------------------------------------------------
# Hierarchical crews add a synthetic manager
# ---------------------------------------------------------------------------

def test_hierarchical_crew_adds_manager_node(hierarchical_design: Design) -> None:
    nodes, edges = graph.design_to_graph(hierarchical_design)
    kinds = [n.kind for n in nodes]
    assert "manager" in kinds
    manager_node = next(n for n in nodes if n.kind == "manager")
    assert manager_node.id == "agent:__manager__"
    assert manager_node.data.get("synthetic") is True


def test_hierarchical_crew_wires_manager_to_every_non_manager_agent(
    hierarchical_design: Design,
) -> None:
    nodes, edges = graph.design_to_graph(hierarchical_design)
    manager_edges = [e for e in edges if e.source == "agent:__manager__"]
    non_manager_agent_ids = {n.id for n in nodes if n.kind == "agent"}
    for e in manager_edges:
        assert e.target in non_manager_agent_ids
    # And every non-manager agent is a target of at least one manager edge.
    manager_targets = {e.target for e in manager_edges}
    assert manager_targets == non_manager_agent_ids


def test_sequential_crew_has_no_manager_node(simple_design: Design) -> None:
    """simple_design uses process='sequential' — no synthetic manager."""
    nodes, _ = graph.design_to_graph(simple_design)
    assert "manager" not in [n.kind for n in nodes]


# ---------------------------------------------------------------------------
# Real fixture: the example crew that ships with the app
# ---------------------------------------------------------------------------

def test_example_design_projects_to_expected_shape() -> None:
    """End-to-end shape check on the hand-crafted example. Guards against
    accidental changes to the projection that would silently break the
    Canvas tab's rendering."""
    project = Path(__file__).parent.parent
    d = Design.model_validate(
        json.loads((project / "examples/simple-research-crew/design.json").read_text())
    )
    nodes, edges = graph.design_to_graph(d)

    # Nodes: 2 agents + 2 tasks + 1 tool = 5.
    kinds = [n.kind for n in nodes]
    assert kinds.count("agent") == 2
    assert kinds.count("task") == 2
    assert kinds.count("tool") == 1
    assert "manager" not in kinds  # example uses sequential process

    # Edges: 2 agent→task, 1 task→task context, 1 tool→agent.
    kinds_e = [e.kind for e in edges]
    assert kinds_e.count("agent_task") == 2
    assert kinds_e.count("task_context") == 1
    assert kinds_e.count("agent_tool") == 1


# ---------------------------------------------------------------------------
# Node data carries useful attributes for the canvas sidebar
# ---------------------------------------------------------------------------

def test_agent_node_carries_role_and_goal() -> None:
    d = Design(
        agents=[Agent(name="a", role="analyst", goal="find things", backstory="b")]
    )
    nodes, _ = graph.design_to_graph(d)
    assert nodes[0].data["role"] == "analyst"
    assert nodes[0].data["goal"] == "find things"


def test_task_node_carries_description_and_expected_output() -> None:
    d = Design(
        tasks=[Task(name="t", description="do it", expected_output="done")],
    )
    nodes, _ = graph.design_to_graph(d)
    assert nodes[0].data["description"] == "do it"
    assert nodes[0].data["expected_output"] == "done"


def test_tool_node_carries_kind() -> None:
    d = Design(tools=[ToolConfig(name="t", kind="SerperDevTool")])
    nodes, _ = graph.design_to_graph(d)
    assert nodes[0].data["kind"] == "SerperDevTool"
