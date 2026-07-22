"""Validation for a crew design.

Runs in two passes:

1. **Design-level checks** — cheap, dependency-free. Duplicate names, agent
   references that don't exist, tool references that aren't declared,
   cycles in `context` dependencies, missing manager LLM on hierarchical
   crews, invalid Python identifier for the crew class name. These catch the
   errors users can't recover from downstream.
2. **CrewAI dry-instantiation** — when the `crewai` package is available,
   construct real ``crewai.Agent`` / ``crewai.Task`` / ``crewai.Crew``
   objects from the design and surface any ``ValidationError`` /
   ``TypeError`` inline. This catches schema drift between the designer and
   the installed CrewAI version.

The dry-instantiation step is opt-out at import time: if `crewai` isn't
installed (e.g. during unit tests), Pass 2 is skipped and Pass 1 alone is
returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from keyword import iskeyword

from .models import Design
from .tools_catalog import is_custom, is_unresolved


@dataclass(frozen=True)
class Error:
    """A single validation problem, scoped to a specific field where possible."""

    where: str  # e.g. "agents[0].role" or "crew.name"
    message: str


def validate(design: Design, *, target: str = "crewai") -> list[Error]:
    """Return every problem found in ``design``. Empty list = valid.

    ``target`` selects export-specific rules:

    - ``"crewai"`` (default) — CustomTool blocks (CAS-only); hierarchical
      crews need ``manager_llm``.
    - ``"cas_workflow"`` — CustomTool is allowed when ``python_code`` is
      present; ``manager_llm`` is not required (CAS synthesizes a manager).
    """
    errors: list[Error] = []
    errors.extend(_check_design(design, target=target))
    if not errors and target == "crewai":
        # Only bother with the expensive path if the design is internally
        # consistent — otherwise CrewAI's own errors would just repeat ours.
        # Skip for CAS: CustomTool designs are not meant to dry-instantiate.
        errors.extend(_dry_instantiate(design))
    return errors


# ---------------------------------------------------------------------------
# Pass 1 — design-level checks
# ---------------------------------------------------------------------------

def _check_design(design: Design, *, target: str) -> list[Error]:
    errors: list[Error] = []

    # Duplicate names within each collection would silently overwrite each
    # other in the exported YAML — catch them here.
    _check_duplicates(design.agents, "agents", errors)
    _check_duplicates(design.tasks, "tasks", errors)
    _check_duplicates(design.tools, "tools", errors)

    agent_names = set(design.agent_names())
    task_names = set(design.task_names())
    tool_names = set(design.tool_names())

    # Tools imported from YAML come in with a placeholder kind — the user
    # must pick a real catalog entry on the Tools tab before we can export
    # them (the generator needs `import_module`/`import_name` to render).
    for i, tool in enumerate(design.tools):
        if is_unresolved(tool.kind):
            errors.append(Error(
                f"tools[{i}].kind",
                f"tool {tool.name!r} was imported from YAML — pick a real "
                "type in the Tools tab before exporting.",
            ))
        elif is_custom(tool.kind):
            if not (tool.python_code and tool.python_code.strip()):
                errors.append(Error(
                    f"tools[{i}].python_code",
                    f"CustomTool {tool.name!r} needs tool.py source before "
                    "exporting.",
                ))
            elif target == "crewai":
                errors.append(Error(
                    f"tools[{i}].kind",
                    f"tool {tool.name!r} is a CustomTool (CAS-only) — export "
                    "as an Agent Studio workflow, or replace it with a "
                    "catalog tool for CrewAI project export.",
                ))

    # Agent-level references
    for i, a in enumerate(design.agents):
        for tool in a.tools:
            if tool not in tool_names:
                errors.append(Error(
                    f"agents[{i}].tools",
                    f"agent {a.name!r} references undeclared tool {tool!r}",
                ))

    # Task-level references
    for i, t in enumerate(design.tasks):
        if t.agent and t.agent not in agent_names:
            errors.append(Error(
                f"tasks[{i}].agent",
                f"task {t.name!r} references unknown agent {t.agent!r}",
            ))
        for tool in t.tools:
            if tool not in tool_names:
                errors.append(Error(
                    f"tasks[{i}].tools",
                    f"task {t.name!r} references undeclared tool {tool!r}",
                ))
        for ctx in t.context:
            if ctx not in task_names:
                errors.append(Error(
                    f"tasks[{i}].context",
                    f"task {t.name!r} depends on unknown task {ctx!r}",
                ))
            elif ctx == t.name:
                errors.append(Error(
                    f"tasks[{i}].context",
                    f"task {t.name!r} cannot depend on itself",
                ))

    # Cycles in task context dependencies
    for cycle in _find_context_cycles(design):
        errors.append(Error(
            "tasks.context",
            f"circular dependency: {' -> '.join(cycle + [cycle[0]])}",
        ))

    # Task ordering references must exist
    for name in design.crew.task_order:
        if name not in task_names:
            errors.append(Error(
                "crew.task_order",
                f"task_order references unknown task {name!r}",
            ))

    # Crew name must be a valid Python identifier — it becomes a class name.
    if not design.crew.name.isidentifier() or iskeyword(design.crew.name):
        errors.append(Error(
            "crew.name",
            f"{design.crew.name!r} is not a valid Python class name",
        ))

    # Hierarchical crews need a manager LLM for CrewAI kickoff. CAS export
    # synthesizes its own manager, so skip this when targeting cas_workflow.
    if (
        target == "crewai"
        and design.crew.process == "hierarchical"
        and not design.crew.manager_llm
    ):
        errors.append(Error(
            "crew.manager_llm",
            "hierarchical process requires a manager_llm",
        ))

    return errors


def _check_duplicates(items: list, where: str, errors: list[Error]) -> None:
    seen: set[str] = set()
    for i, item in enumerate(items):
        if item.name in seen:
            errors.append(Error(f"{where}[{i}].name", f"duplicate name {item.name!r}"))
        seen.add(item.name)


def _find_context_cycles(design: Design) -> list[list[str]]:
    """Return a list of cycles in the task-context graph.

    Uses DFS with a coloring set (WHITE = unvisited, GRAY = on stack,
    BLACK = fully processed). Only reports each cycle once.
    """
    graph: dict[str, list[str]] = {t.name: list(t.context) for t in design.tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in graph}
    cycles: list[list[str]] = []
    seen_cycle_keys: set[frozenset] = set()

    def dfs(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in color:
                continue  # missing-ref error was already reported
            if color[nxt] == GRAY:
                cycle = stack[stack.index(nxt):]
                key = frozenset(cycle)
                if key not in seen_cycle_keys:
                    seen_cycle_keys.add(key)
                    cycles.append(cycle)
            elif color[nxt] == WHITE:
                dfs(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for n in list(graph):
        if color[n] == WHITE:
            dfs(n, [])
    return cycles


# ---------------------------------------------------------------------------
# Pass 2 — CrewAI dry-instantiation
# ---------------------------------------------------------------------------

def _dry_instantiate(design: Design) -> list[Error]:
    """Try to build real CrewAI objects. Returns any Pydantic/type errors.

    Silently skipped if `crewai` isn't installed — that's expected in unit
    tests and CI environments that only care about the design-level pass.
    """
    try:
        from crewai import Agent as CAAgent
        from crewai import Task as CATask
    except Exception:
        return []  # crewai not importable — skip

    errors: list[Error] = []
    built_agents: dict[str, object] = {}

    for i, a in enumerate(design.agents):
        try:
            built_agents[a.name] = CAAgent(
                role=a.role,
                goal=a.goal,
                backstory=a.backstory,
                # Skip llm/tools — those need real objects and pull in extra
                # deps. Field-level validation of prose + basic options is
                # what we need here.
                max_iter=a.max_iter,
                verbose=a.verbose,
                allow_delegation=a.allow_delegation,
                reasoning=a.reasoning,
                multimodal=a.multimodal,
                respect_context_window=a.respect_context_window,
                max_retry_limit=a.max_retry_limit,
                inject_date=a.inject_date,
                date_format=a.date_format,
            )
        except Exception as e:
            errors.append(Error(f"agents[{i}]", f"crewai rejected agent: {e}"))

    for i, t in enumerate(design.tasks):
        agent = built_agents.get(t.agent) if t.agent else None
        try:
            CATask(
                description=t.description,
                expected_output=t.expected_output,
                agent=agent,
                async_execution=t.async_execution,
                human_input=t.human_input,
                markdown=t.markdown,
                output_file=t.output_file,
            )
        except Exception as e:
            errors.append(Error(f"tasks[{i}]", f"crewai rejected task: {e}"))

    return errors
