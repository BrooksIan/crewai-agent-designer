"""Project a `Design` into a graph the Canvas tab can render.

Pure, library-agnostic module. Emits two dataclasses (:class:`Node` and
:class:`Edge`) that either `streamlit-flow-component` or `streamlit-agraph`
can consume via a thin adapter in ``app/tabs/canvas.py``.

Why not go directly to the canvas library's own types here? Because:

- **Tests stay dependency-light.** Snapshot tests for the projection
  don't need Streamlit or React Flow installed.
- **Fallback library switch is local.** If we swap `streamlit-flow` for
  a different renderer later, only the canvas tab changes — not the
  projection.
- **Determinism is easier to reason about** when the outputs are plain
  Python data.

The projection is a *read-only* view of the `Design` — Phase 1 of the
workflow-designer roadmap. Later phases add edit-through-canvas actions,
but those write back to the `Design` and the projection recomputes on
the next render; no state lives in the graph itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .models import Design


# Node "kind" drives visual styling in the canvas tab (color, icon).
# ``manager`` is only emitted for hierarchical crews and represents the
# synthetic manager agent that CAS-style workflows route through.
NodeKind = Literal["agent", "task", "tool", "manager"]

# Edge "kind" tells the canvas how to style each edge and — in later
# phases — what field on the `Design` to mutate when the edge is deleted.
EdgeKind = Literal["agent_task", "task_context", "agent_tool"]


@dataclass(frozen=True)
class Node:
    """Single canvas node.

    ``id`` is stable across renders for a given design so React Flow
    can preserve positions and animations. Prefix guarantees uniqueness
    across kinds (e.g. an agent named "search" and a tool named "search"
    can coexist).

    ``label`` is what the user sees on the node. ``data`` carries the
    original object's key attributes (role, description, kind) so a
    click-handler can populate a sidebar without a second lookup.
    """

    id: str
    kind: NodeKind
    label: str
    data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    """Directed edge between two nodes.

    ``kind`` is what makes edge deletion write-back tractable in Phase 3:
    an ``agent_task`` edge deleted means "unset `Task.agent`", a
    ``task_context`` edge deleted means "drop that entry from
    `Task.context`", etc. We surface the kind here so the canvas tab
    doesn't have to re-derive it.
    """

    id: str
    source: str  # source Node.id
    target: str  # target Node.id
    kind: EdgeKind
    label: str = ""


# ---------------------------------------------------------------------------
# ID helpers — stable across renders, unique across kinds
# ---------------------------------------------------------------------------


def _agent_id(name: str) -> str:
    return f"agent:{name}"


def _task_id(name: str) -> str:
    return f"task:{name}"


def _tool_id(name: str) -> str:
    return f"tool:{name}"


def _manager_id() -> str:
    # Fixed — a design has at most one synthetic manager.
    return "agent:__manager__"


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def design_to_graph(design: Design) -> tuple[list[Node], list[Edge]]:
    """Return (nodes, edges) for the given `Design`.

    Deterministic: same design → same graph, in the same order. Emit
    order is agents → manager → tasks → tools so the canvas layout
    engine has a predictable starting point for its tree layout.

    Edges are only emitted between nodes that exist. A `Task.agent`
    pointing at a missing agent doesn't crash — it's just skipped. The
    same tolerance covers YAML imports that referenced unknown names
    (already surfaced as warnings by :mod:`app.import_yaml`).
    """
    nodes: list[Node] = []
    edges: list[Edge] = []

    # -- Nodes --

    for agent in design.agents:
        nodes.append(
            Node(
                id=_agent_id(agent.name),
                kind="agent",
                label=agent.name,
                data={"role": agent.role, "goal": agent.goal},
            )
        )

    # Synthesize the manager node for hierarchical crews so users can see
    # the routing agent the CAS export will inject. Nothing lives in the
    # `Design` for this — it's a canvas-only affordance.
    if design.crew.process == "hierarchical":
        nodes.append(
            Node(
                id=_manager_id(),
                kind="manager",
                label="Workflow Manager",
                data={"synthetic": True},
            )
        )

    for task in design.tasks:
        nodes.append(
            Node(
                id=_task_id(task.name),
                kind="task",
                label=task.name,
                data={
                    "description": task.description,
                    "expected_output": task.expected_output,
                },
            )
        )

    for tool in design.tools:
        nodes.append(
            Node(
                id=_tool_id(tool.name),
                kind="tool",
                label=tool.name,
                data={"kind": tool.kind},
            )
        )

    # -- Edges --
    # Build a lookup once so we can validate every ref before emitting.
    node_ids = {n.id for n in nodes}

    for task in design.tasks:
        # agent → task (an agent "runs" its assigned task).
        if task.agent:
            src = _agent_id(task.agent)
            if src in node_ids:
                edges.append(
                    Edge(
                        id=f"e:agent_task:{task.agent}->{task.name}",
                        source=src,
                        target=_task_id(task.name),
                        kind="agent_task",
                    )
                )
        # task → task (context deps: source outputs feed the target).
        for ctx in task.context:
            src = _task_id(ctx)
            if src in node_ids:
                edges.append(
                    Edge(
                        id=f"e:task_context:{ctx}->{task.name}",
                        source=src,
                        target=_task_id(task.name),
                        kind="task_context",
                    )
                )

    # tool → agent (an agent "has" its bound tool).
    for agent in design.agents:
        for tool_name in agent.tools:
            src = _tool_id(tool_name)
            if src in node_ids:
                edges.append(
                    Edge(
                        id=f"e:agent_tool:{tool_name}->{agent.name}",
                        source=src,
                        target=_agent_id(agent.name),
                        kind="agent_tool",
                    )
                )

    # For hierarchical crews, wire the manager to every non-manager
    # agent so users can see the delegation topology. The Design itself
    # has no field for these edges — they're canvas-only, same as the
    # manager node.
    if design.crew.process == "hierarchical":
        for agent in design.agents:
            edges.append(
                Edge(
                    id=f"e:manager:{agent.name}",
                    source=_manager_id(),
                    target=_agent_id(agent.name),
                    kind="agent_task",  # visual style same as delegate edge
                    label="delegates to",
                )
            )

    return nodes, edges


# ---------------------------------------------------------------------------
# Layout — stable column positions for the canvas renderer
# ---------------------------------------------------------------------------

# Left → right columns match data flow: tools feed agents, agents run tasks.
_COLUMN_X: dict[NodeKind, float] = {
    "tool": 0.0,
    "manager": 320.0,
    "agent": 320.0,
    "task": 640.0,
}
_ROW_GAP = 120.0  # vertical space between peers in the same column


def layout_positions(nodes: list[Node]) -> dict[str, tuple[float, float]]:
    """Return ``{node_id: (x, y)}`` with kinds in separate columns.

    Tree/force layouts tend to stack agents and tasks on top of each other
    for this graph shape (multiple roots, cross-kind edges). Explicit
    columns keep peers readable without depending on ELK.

    Within a column, nodes keep their projection order and are spaced
    ``_ROW_GAP`` apart on the y-axis. Deterministic for a given node list.
    """
    # Preserve encounter order per kind so layout stays stable across reruns.
    by_kind: dict[NodeKind, list[Node]] = {
        "tool": [],
        "manager": [],
        "agent": [],
        "task": [],
    }
    for node in nodes:
        by_kind[node.kind].append(node)

    positions: dict[str, tuple[float, float]] = {}
    # Manager sits above agents in the shared middle column.
    for kind in ("tool", "manager", "agent", "task"):
        x = _COLUMN_X[kind]
        # Agents continue below any manager already placed in this column.
        y_offset = 0.0
        if kind == "agent" and by_kind["manager"]:
            y_offset = len(by_kind["manager"]) * _ROW_GAP
        for i, node in enumerate(by_kind[kind]):
            positions[node.id] = (x, y_offset + i * _ROW_GAP)
    return positions
