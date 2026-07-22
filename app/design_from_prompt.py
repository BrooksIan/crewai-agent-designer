"""Assemble a `Design` from an LLM :class:`~app.llm.DesignDraft`.

Pure module — the Generate tab calls :func:`from_draft` after
``llm_client.draft_design`` returns. Keeps identifier sanitization, tool
catalog resolution, and warnings out of the Streamlit layer so the
assembler is unit-tested without UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from keyword import iskeyword
from typing import Any

from .llm import DesignDraft
from .models import Agent, Crew, Design, Task, ToolConfig
from .tools_catalog import CATALOG, by_kind, is_custom


class AssembleError(Exception):
    """Fatal failure turning a draft into a Design (e.g. no agents)."""


@dataclass
class AssembleResult:
    """Return value of :func:`from_draft`."""

    design: Design
    warnings: list[str] = field(default_factory=list)


_IDENT_RE = re.compile(r"[^0-9A-Za-z_]+")


def from_draft(draft: DesignDraft) -> AssembleResult:
    """Convert a :class:`DesignDraft` into a validated-shape `Design`.

    Unknown catalog tool kinds are dropped with warnings. Names are coerced
    to unique Python identifiers. Raises :class:`AssembleError` when the
    draft has no agents (nothing useful to load into the designer).
    """
    warnings: list[str] = []
    if not draft.agents:
        raise AssembleError("Draft has no agents — nothing to generate.")

    tools, tool_name_map = _assemble_tools(draft, warnings)
    agents = _assemble_agents(draft, tool_name_map, warnings)
    if not agents:
        raise AssembleError("Draft produced no agents after sanitization.")
    agent_names = {a.name for a in agents}
    tasks = _assemble_tasks(draft, agent_names, warnings)
    crew = _assemble_crew(draft, warnings)

    design = Design(agents=agents, tasks=tasks, tools=tools, crew=crew)
    return AssembleResult(design=design, warnings=warnings)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _assemble_tools(
    draft: DesignDraft, warnings: list[str]
) -> tuple[list[ToolConfig], dict[str, str]]:
    """Build ToolConfigs and a map from draft tool names → final names."""
    label_to_kind = {
        e.label.lower(): e.kind for e in CATALOG if not is_custom(e.kind)
    }
    kind_set = {e.kind for e in CATALOG if not is_custom(e.kind)}

    used: set[str] = set()
    tools: list[ToolConfig] = []
    name_map: dict[str, str] = {}

    for i, spec in enumerate(draft.tools):
        kind = _resolve_kind(spec.kind, kind_set, label_to_kind)
        if kind is None:
            warnings.append(
                f"tool {spec.name!r}: unknown kind {spec.kind!r} — dropped "
                "(v1 Generate only supports catalog tools)."
            )
            continue
        raw_name = spec.name or f"tool_{i + 1}"
        name = _unique_name(_to_identifier(raw_name, fallback=f"tool_{i + 1}"), used)
        used.add(name)
        name_map[spec.name] = name
        if spec.name != name:
            name_map[name] = name
        params = _filter_params(kind, spec.params, warnings, tool_name=name)
        tools.append(ToolConfig(name=name, kind=kind, params=params))

    # Materialize tools referenced by agents that were never declared in tools[].
    for agent in draft.agents:
        for ref in agent.tools:
            if ref in name_map:
                continue
            kind = _resolve_kind(ref, kind_set, label_to_kind)
            if kind is None:
                continue
            name = _unique_name(_to_identifier(kind, fallback="tool"), used)
            used.add(name)
            name_map[ref] = name
            tools.append(ToolConfig(name=name, kind=kind, params={}))
            warnings.append(
                f"tool {name!r} ({kind}) inferred from agent tool ref {ref!r}."
            )

    return tools, name_map


def _resolve_kind(
    raw: str, kind_set: set[str], label_to_kind: dict[str, str]
) -> str | None:
    if not raw:
        return None
    if raw in kind_set:
        return raw
    mapped = label_to_kind.get(raw.lower())
    if mapped:
        return mapped
    # Case-insensitive kind match
    lower = {k.lower(): k for k in kind_set}
    return lower.get(raw.lower())


def _filter_params(
    kind: str, params: dict[str, Any], warnings: list[str], *, tool_name: str
) -> dict[str, Any]:
    entry = by_kind(kind)
    if entry is None:
        return {}
    allowed = {p.name for p in entry.params}
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key in allowed:
            out[key] = value
        else:
            warnings.append(
                f"tool {tool_name!r}: ignored unknown param {key!r}."
            )
    return out


# ---------------------------------------------------------------------------
# Agents / tasks / crew
# ---------------------------------------------------------------------------

def _assemble_agents(
    draft: DesignDraft,
    tool_name_map: dict[str, str],
    warnings: list[str],
) -> list[Agent]:
    used: set[str] = set()
    agents: list[Agent] = []
    for i, spec in enumerate(draft.agents):
        name = _unique_name(
            _to_identifier(spec.name, fallback=f"agent_{i + 1}"), used
        )
        used.add(name)
        tool_refs: list[str] = []
        for ref in spec.tools:
            mapped = tool_name_map.get(ref)
            if mapped:
                if mapped not in tool_refs:
                    tool_refs.append(mapped)
            else:
                warnings.append(
                    f"agent {name!r}: unknown tool ref {ref!r} — dropped."
                )
        agents.append(
            Agent(
                name=name,
                role=spec.role or name,
                goal=spec.goal or "Complete assigned tasks.",
                backstory=spec.backstory or "A capable specialist.",
                tools=tool_refs,
                allow_delegation=spec.allow_delegation,
            )
        )
    return agents


def _assemble_tasks(
    draft: DesignDraft,
    agent_names: set[str],
    warnings: list[str],
) -> list[Task]:
    used: set[str] = set()
    # Map draft agent names → sanitized names via fuzzy: we rebuild from
    # draft order matching assembled agents by index when names diverge.
    draft_to_final: dict[str, str] = {}
    # Prefer exact sanitized match against agent_names.
    for spec_name in (a.name for a in draft.agents):
        sanitized = _to_identifier(spec_name, fallback=spec_name)
        if sanitized in agent_names:
            draft_to_final[spec_name] = sanitized
        elif spec_name in agent_names:
            draft_to_final[spec_name] = spec_name

    # Fill remaining by unique-name algorithm matching from_draft agents order.
    used_agent = set()
    for i, spec in enumerate(draft.agents):
        final = _unique_name(
            _to_identifier(spec.name, fallback=f"agent_{i + 1}"), used_agent
        )
        used_agent.add(final)
        draft_to_final[spec.name] = final

    tasks: list[Task] = []
    draft_task_names: list[str] = []
    for i, spec in enumerate(draft.tasks):
        name = _unique_name(
            _to_identifier(spec.name, fallback=f"task_{i + 1}"), used
        )
        used.add(name)
        draft_task_names.append(name)

        agent_ref = None
        if spec.agent:
            agent_ref = draft_to_final.get(spec.agent) or (
                spec.agent if spec.agent in agent_names else None
            )
            if agent_ref is None:
                warnings.append(
                    f"task {name!r}: unknown agent {spec.agent!r} — left unassigned."
                )

        tasks.append(
            Task(
                name=name,
                description=spec.description,
                expected_output=spec.expected_output,
                agent=agent_ref,
                context=[],  # resolve after all names known
            )
        )

    # Second pass for context refs (draft name → final name).
    draft_task_map = {
        draft.tasks[i].name: draft_task_names[i]
        for i in range(len(draft.tasks))
    }
    for i, spec in enumerate(draft.tasks):
        ctx: list[str] = []
        for ref in spec.context:
            mapped = draft_task_map.get(ref) or (ref if ref in used else None)
            if mapped and mapped != tasks[i].name:
                ctx.append(mapped)
            elif mapped == tasks[i].name:
                warnings.append(
                    f"task {tasks[i].name!r}: ignored self-referential context."
                )
            else:
                warnings.append(
                    f"task {tasks[i].name!r}: unknown context task {ref!r} — dropped."
                )
        tasks[i].context = ctx

    return tasks


def _assemble_crew(draft: DesignDraft, warnings: list[str]) -> Crew:
    raw_name = draft.crew.name or "GeneratedCrew"
    name = _to_identifier(raw_name, fallback="GeneratedCrew")
    if name[0].isdigit():
        name = f"Crew_{name}"
    if iskeyword(name):
        name = f"{name}_crew"
    # Prefer CapWords for class names when the model used snake_case.
    if "_" in name and name == name.lower():
        name = "".join(part.capitalize() for part in name.split("_") if part) or name
    if not name.isidentifier():
        name = "GeneratedCrew"
    if name != raw_name:
        warnings.append(
            f"crew name {raw_name!r} sanitized to valid identifier {name!r}."
        )

    process = draft.crew.process if draft.crew.process in (
        "sequential",
        "hierarchical",
    ) else "sequential"
    manager_llm = draft.crew.manager_llm
    if process == "hierarchical" and not manager_llm:
        manager_llm = "gpt-4o"
        warnings.append(
            "hierarchical crew had no manager_llm — defaulted to 'gpt-4o' "
            "(edit on the Crew tab)."
        )

    return Crew(name=name, process=process, manager_llm=manager_llm)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def _to_identifier(raw: str, *, fallback: str) -> str:
    s = _IDENT_RE.sub("_", raw).strip("_")
    if not s:
        s = _IDENT_RE.sub("_", fallback).strip("_") or "item"
    if s[0].isdigit():
        s = f"n_{s}"
    if iskeyword(s):
        s = f"{s}_"
    if not s.isidentifier():
        s = "item"
    return s


def _unique_name(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"
