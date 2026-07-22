"""Pure functions that mutate a `Design` in response to canvas actions.

The Canvas tab (Phase 2 of the workflow-designer roadmap) fires actions
like "delete this agent" or "delete this task" when the user hits the
delete button after selecting a node. Doing the mutation inline in the
tab would put reference-cleanup logic next to Streamlit rendering — not
where anyone thinks to look for it, and impossible to unit-test in
isolation.

This module holds the mutations. Each function:

- Takes a `Design` and mutates it in place (matches how the tab-based
  edit forms already work — Streamlit reruns with the mutated design
  after every action).
- Returns a list of **cleanup notes** describing every cross-reference
  that got scrubbed. The canvas surfaces these as ``st.warning``s so
  users understand what "delete this task" also touched.
- Never raises for a missing entity — silently no-op on a name that
  doesn't exist. Canvas actions can race with tab-tab edits; a fast
  no-op is better than an unfamiliar traceback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from .models import Crew, Design


class Named(Protocol):
    name: str


TNamed = TypeVar("TNamed", bound=Named)


def clear_design(design: Design) -> None:
    """Wipe agents, tasks, and tools; reset crew to defaults.

    Preserves ``workplace`` so clearing the canvas doesn't unassign the
    design from its workplace. Callers replace ``st.session_state["design"]``
    with the mutated object (or a fresh ``Design``) after calling this.
    """
    workplace = design.workplace
    design.agents.clear()
    design.tasks.clear()
    design.tools.clear()
    design.crew = Crew()
    design.workplace = workplace


def delete_agent(design: Design, name: str) -> list[str]:
    """Remove an agent and scrub every reference to it.

    Cross-references cleaned:
    - ``Task.agent`` — set to ``None`` for every task pointing at ``name``.
      (An orphan task is a validation error users can then fix; the
      alternative — silently deleting the task too — is more surprising.)
    """
    warnings: list[str] = []
    idx = _find_index(design.agents, name)
    if idx is None:
        return warnings
    del design.agents[idx]

    for task in design.tasks:
        if task.agent == name:
            task.agent = None
            warnings.append(
                f"Task {task.name!r} was assigned to {name!r} — its agent "
                "is now unset. Pick a new agent on the Tasks tab."
            )
    return warnings


def delete_task(design: Design, name: str) -> list[str]:
    """Remove a task and scrub every reference to it.

    Cross-references cleaned:
    - Every other task's ``context`` list — the deleted task's name is
      dropped from any downstream tasks that depended on its output.
    - ``design.crew.task_order`` — the deleted task's name is dropped so
      the exported YAML doesn't reference it.
    """
    warnings: list[str] = []
    idx = _find_index(design.tasks, name)
    if idx is None:
        return warnings
    del design.tasks[idx]

    for other in design.tasks:
        if name in other.context:
            other.context = [c for c in other.context if c != name]
            warnings.append(
                f"Task {other.name!r} depended on {name!r} in its context "
                "— that dependency was removed."
            )

    if name in design.crew.task_order:
        design.crew.task_order = [n for n in design.crew.task_order if n != name]
        warnings.append(f"Removed {name!r} from the Crew tab's task execution order.")

    return warnings


def delete_tool(design: Design, name: str) -> list[str]:
    """Remove a tool and scrub every reference to it.

    Cross-references cleaned:
    - Every ``Agent.tools`` list.
    - Every ``Task.tools`` list.

    Rationale for scrubbing rather than blocking the delete: the exporter
    treats a reference to a missing tool as an error anyway. Silently
    scrubbing gives the user one action instead of a "you must first…"
    error loop.
    """
    warnings: list[str] = []
    idx = _find_index(design.tools, name)
    if idx is None:
        return warnings
    del design.tools[idx]

    for agent in design.agents:
        if name in agent.tools:
            agent.tools = [t for t in agent.tools if t != name]
            warnings.append(
                f"Agent {agent.name!r} was using tool {name!r} — the "
                "binding was removed."
            )

    for task in design.tasks:
        if name in task.tools:
            task.tools = [t for t in task.tools if t != name]
            warnings.append(
                f"Task {task.name!r} was using tool {name!r} — the "
                "binding was removed."
            )
    return warnings


def enable_manager(design: Design, manager_llm: str) -> list[str]:
    """Turn on hierarchical process and set the manager LLM.

    The canvas synthesizes a manager node when ``process == "hierarchical"``.
    Returns a single warning string if ``manager_llm`` is blank (and does
    not mutate the design in that case).
    """
    llm = manager_llm.strip()
    if not llm:
        return ["Manager LLM is required to add a manager."]
    design.crew.process = "hierarchical"
    design.crew.manager_llm = llm
    return []


def update_manager_llm(design: Design, manager_llm: str) -> list[str]:
    """Update the manager LLM on an already-hierarchical crew.

    No-ops with a warning if the crew is still sequential or the LLM
    string is blank.
    """
    llm = manager_llm.strip()
    if design.crew.process != "hierarchical":
        return ["Enable a manager first (hierarchical process)."]
    if not llm:
        return ["Manager LLM cannot be empty."]
    design.crew.manager_llm = llm
    return []


def disable_manager(design: Design) -> list[str]:
    """Remove the synthetic manager by switching back to sequential process."""
    design.crew.process = "sequential"
    design.crew.manager_llm = None
    return []


def _find_index(items: list[TNamed], name: str) -> int | None:
    """Return the index of an item with ``.name == name``, or None."""
    for i, item in enumerate(items):
        if item.name == name:
            return i
    return None


# ---------------------------------------------------------------------------
# Edge actions (Phase 3)
# ---------------------------------------------------------------------------

# Canvas node ids are ``"<kind>:<name>"`` (see `app.graph`). Edges the
# flow component emits carry these ids verbatim, so we split them back
# out to identify what to mutate.


@dataclass(frozen=True)
class EdgeResult:
    """Result of applying or removing a canvas edge.

    - ``ok=True`` means the Design was mutated.
    - ``ok=False`` means we refused the operation for a specific reason
      the canvas can surface to the user (e.g. "agents can't connect to
      other agents"). ``reason`` is the human-readable string.
    """

    ok: bool
    reason: str = ""


def _split_id(node_id: str) -> tuple[str | None, str | None]:
    """Parse a canvas node id back into ``(kind, name)`` or ``(None, None)``."""
    if not isinstance(node_id, str) or ":" not in node_id:
        return None, None
    kind, _, name = node_id.partition(":")
    if kind not in ("agent", "task", "tool", "manager"):
        return None, None
    return kind, name


def apply_edge(design: Design, source_id: str, target_id: str) -> EdgeResult:
    """Add a canvas edge to the underlying `Design`.

    Edge direction is meaningful; the pair ``(source_kind, target_kind)``
    determines which field on the Design gets mutated:

    - ``agent → task``  → sets ``Task.agent`` to the agent's name.
    - ``task → task``   → appends the source task's name to the target
      task's ``context`` list.
    - ``tool → agent``  → appends the tool's name to the agent's
      ``tools`` list.

    Every other combination is refused with an explanatory reason —
    that's the pure-function analog of highlighting the offending edge
    red on the canvas. The caller (diff-sync in the Canvas tab) rolls
    back the visual edge when this returns ``ok=False``.

    Idempotent: reapplying an existing edge is a no-op (returns ``ok=True``
    so the caller can treat it uniformly with fresh applies).
    """
    src_kind, src_name = _split_id(source_id)
    tgt_kind, tgt_name = _split_id(target_id)

    if src_kind is None or tgt_kind is None:
        return EdgeResult(False, f"Unknown canvas node: {source_id!r} → {target_id!r}")

    if src_kind == "agent" and tgt_kind == "task":
        return _apply_agent_task(design, src_name, tgt_name)
    if src_kind == "task" and tgt_kind == "task":
        return _apply_task_context(design, src_name, tgt_name)
    if src_kind == "tool" and tgt_kind == "agent":
        return _apply_agent_tool(design, src_name, tgt_name)

    return EdgeResult(
        False,
        f"Not a valid connection: a {src_kind!r} can't connect to a {tgt_kind!r}.",
    )


def remove_edge(design: Design, source_id: str, target_id: str) -> EdgeResult:
    """Remove a canvas edge from the underlying `Design`.

    Mirror of :func:`apply_edge`. Idempotent — deleting an edge that no
    longer exists is treated as success (the caller doesn't need to
    distinguish "we cleaned up" from "already clean").
    """
    src_kind, src_name = _split_id(source_id)
    tgt_kind, tgt_name = _split_id(target_id)

    if src_kind is None or tgt_kind is None:
        return EdgeResult(True)  # silently ignore mystery edges

    if src_kind == "agent" and tgt_kind == "task":
        return _remove_agent_task(design, src_name, tgt_name)
    if src_kind == "task" and tgt_kind == "task":
        return _remove_task_context(design, src_name, tgt_name)
    if src_kind == "tool" and tgt_kind == "agent":
        return _remove_agent_tool(design, src_name, tgt_name)

    return EdgeResult(True)  # nothing to remove for a combination we never wrote


# ---------------------------------------------------------------------------
# Per-kind apply/remove (internal)
# ---------------------------------------------------------------------------

def _apply_agent_task(design: Design, agent_name: str, task_name: str) -> EdgeResult:
    """Set ``Task.agent`` for the target task."""
    if _find_index(design.agents, agent_name) is None:
        return EdgeResult(False, f"Agent {agent_name!r} doesn't exist.")
    task_idx = _find_index(design.tasks, task_name)
    if task_idx is None:
        return EdgeResult(False, f"Task {task_name!r} doesn't exist.")
    design.tasks[task_idx].agent = agent_name
    return EdgeResult(True)


def _remove_agent_task(design: Design, agent_name: str, task_name: str) -> EdgeResult:
    task_idx = _find_index(design.tasks, task_name)
    if task_idx is None:
        return EdgeResult(True)
    if design.tasks[task_idx].agent == agent_name:
        design.tasks[task_idx].agent = None
    return EdgeResult(True)


def _apply_task_context(design: Design, src_task: str, tgt_task: str) -> EdgeResult:
    """Append ``src_task`` to ``tgt_task``'s context list.

    Refuses self-loops (a task depending on itself) — the validator would
    reject that later, but the canvas should reject it *at draw time* so
    the user sees the feedback where they made the mistake.
    """
    if src_task == tgt_task:
        return EdgeResult(False, "A task can't depend on itself.")
    if _find_index(design.tasks, src_task) is None:
        return EdgeResult(False, f"Task {src_task!r} doesn't exist.")
    tgt_idx = _find_index(design.tasks, tgt_task)
    if tgt_idx is None:
        return EdgeResult(False, f"Task {tgt_task!r} doesn't exist.")

    task = design.tasks[tgt_idx]
    if src_task not in task.context:
        task.context = list(task.context) + [src_task]
    return EdgeResult(True)


def _remove_task_context(design: Design, src_task: str, tgt_task: str) -> EdgeResult:
    tgt_idx = _find_index(design.tasks, tgt_task)
    if tgt_idx is None:
        return EdgeResult(True)
    task = design.tasks[tgt_idx]
    if src_task in task.context:
        task.context = [c for c in task.context if c != src_task]
    return EdgeResult(True)


def _apply_agent_tool(design: Design, tool_name: str, agent_name: str) -> EdgeResult:
    """Append ``tool_name`` to the agent's ``tools`` list."""
    if _find_index(design.tools, tool_name) is None:
        return EdgeResult(False, f"Tool {tool_name!r} doesn't exist.")
    agent_idx = _find_index(design.agents, agent_name)
    if agent_idx is None:
        return EdgeResult(False, f"Agent {agent_name!r} doesn't exist.")

    agent = design.agents[agent_idx]
    if tool_name not in agent.tools:
        agent.tools = list(agent.tools) + [tool_name]
    return EdgeResult(True)


def _remove_agent_tool(design: Design, tool_name: str, agent_name: str) -> EdgeResult:
    agent_idx = _find_index(design.agents, agent_name)
    if agent_idx is None:
        return EdgeResult(True)
    agent = design.agents[agent_idx]
    if tool_name in agent.tools:
        agent.tools = [t for t in agent.tools if t != tool_name]
    return EdgeResult(True)


# ---------------------------------------------------------------------------
# Diff sync — apply canvas edge changes to the Design in one pass
# ---------------------------------------------------------------------------

@dataclass
class SyncReport:
    """Outcome of one canvas → Design sync pass.

    - ``applied`` — number of new edges we accepted and wrote into the
      Design. Purely informational; the canvas doesn't need to render this.
    - ``removed`` — number of edges the user deleted on the canvas that
      we scrubbed from the Design.
    - ``rejected`` — list of ``(source_id, target_id, reason)`` for edges
      the user tried to draw that we refused. The canvas surfaces each
      as a warning and rolls back the visual edge.
    """

    applied: int = 0
    removed: int = 0
    rejected: list[tuple[str, str, str]] = field(default_factory=list)


def sync_edges(
    design: Design,
    before: set[tuple[str, str]],
    after: set[tuple[str, str]],
) -> SyncReport:
    """Bring ``design`` in line with the ``after`` edge set.

    ``before`` and ``after`` are sets of ``(source_id, target_id)`` tuples.
    The typical caller (Canvas tab) derives ``before`` from
    :func:`app.graph.design_to_graph` and ``after`` from the flow
    component's live state, then calls this to converge them.

    Order of operations:

    1. **Remove** edges that were in ``before`` but not in ``after`` —
       user deleted them on the canvas. Removes are always safe.
    2. **Apply** edges that are in ``after`` but not in ``before`` — user
       drew them. Every apply is validated for cycles *after* the mutation;
       if a cycle appears, we roll that specific edge back and record it
       in ``rejected``.

    Rejections don't stop other edges from applying — a batch that
    contains one bad edge and three good ones still lands the three.
    """
    from . import validate  # local import to avoid a cycle at module load

    report = SyncReport()

    for src, tgt in before - after:
        remove_edge(design, src, tgt)
        report.removed += 1

    for src, tgt in after - before:
        result = apply_edge(design, src, tgt)
        if not result.ok:
            report.rejected.append((src, tgt, result.reason))
            continue

        # Validate after each apply. If the new edge introduces a cycle
        # in task context deps, roll THAT edge back — leaving the rest
        # of the sync intact.
        errors = validate.validate(design)
        cycle_errs = [e for e in errors if "circular" in e.message]
        if cycle_errs:
            remove_edge(design, src, tgt)
            report.rejected.append(
                (src, tgt, f"Would create a cycle: {cycle_errs[0].message}")
            )
            continue

        report.applied += 1

    return report
