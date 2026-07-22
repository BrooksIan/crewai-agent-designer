"""Import CrewAI ``agents.yaml`` + ``tasks.yaml`` into a `Design`.

Pure module — the Streamlit UI in the sidebar just calls :func:`from_yaml`
and displays whatever `ImportResult` comes back. Keeping the parser
separate from the widget lets us round-trip against the generator in tests
without any of the UI machinery.

Compatibility notes:

- Our exporter (:mod:`app.generate`) emits a strict subset of the CrewAI
  YAML schema. Round-tripping our own output is the acceptance criterion
  for this module — a value we write must survive being read back.
- Hand-authored YAML from other CrewAI projects may include fields we
  don't model (e.g. `system_template`, `prompt_template`, `config`,
  `output_json`). Unknown keys are dropped with a warning rather than
  rejected — an import that partially succeeds is more useful than an
  import that fails outright.
- Tool references (`agent.tools` / `task.tools`) name tools without
  declaring their kind (CrewAI resolves the type at `crew.py` import
  time). We materialize each referenced-but-undeclared name as a
  ``ToolConfig(kind="_unknown_")``; the user resolves the real kind on
  the Tools tab before the design can be exported (see
  :mod:`app.validate`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import ValidationError

from .models import Agent, Design, Task, ToolConfig
from .tools_catalog import UNKNOWN_TOOL_KIND as UNKNOWN_TOOL_KIND


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
# UNKNOWN_TOOL_KIND is re-exported from tools_catalog so importers and tests
# can keep ``from app.import_yaml import UNKNOWN_TOOL_KIND``.


class ImportError(Exception):
    """Base class for every error raised during YAML import.

    Streamlit catches this and displays the message to the user, so
    subclasses should carry enough context to point at the field / agent /
    task that failed. Never subclass `Exception` directly downstream —
    catch `ImportError` for a unified error surface.
    """


class InvalidYamlError(ImportError):
    """Raised when the YAML text is syntactically invalid or the top-level
    shape isn't a mapping (CrewAI YAML docs are always ``name: {...}``)."""


class SchemaError(ImportError):
    """Raised when the parsed YAML shape is valid YAML but violates the
    CrewAI schema in a way that leaves us with no field to write into."""


@dataclass
class ImportResult:
    """Return value of :func:`from_yaml`.

    ``design`` is the parsed `Design`. ``warnings`` collects everything
    non-fatal — unknown-tool references, ignored extra keys, missing agent
    refs. The sidebar renders every warning as an `st.warning` so the user
    sees exactly what was projected.
    """

    design: Design
    warnings: list[str] = field(default_factory=list)


def from_yaml(agents_yaml: str, tasks_yaml: str) -> ImportResult:
    """Parse two CrewAI YAML documents and assemble a `Design`.

    Any warnings (unknown tools, extra keys, unresolved agent refs) are
    attached to :attr:`ImportResult.warnings`. Fatal errors raise a
    subclass of :class:`ImportError`.
    """
    warnings: list[str] = []
    agents_doc = _load_top_level_mapping(agents_yaml, source="agents.yaml")
    tasks_doc = _load_top_level_mapping(tasks_yaml, source="tasks.yaml")

    agents = [_parse_agent(name, body, warnings) for name, body in agents_doc.items()]
    agent_names = {a.name for a in agents}

    tasks = [
        _parse_task(name, body, agent_names, warnings)
        for name, body in tasks_doc.items()
    ]

    # A ToolConfig for every unique tool name referenced from an agent or a
    # task. The user resolves the real kind on the Tools tab before export.
    tool_configs = _synthesize_tool_configs(agents, tasks, warnings)

    design = Design(agents=agents, tasks=tasks, tools=tool_configs)
    return ImportResult(design=design, warnings=warnings)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_top_level_mapping(text: str, *, source: str) -> dict[str, Any]:
    """Parse YAML text and confirm the top level is a mapping.

    Empty documents (``None`` after safe_load) are treated as empty
    mappings — importing a design with zero agents (from an empty
    ``agents.yaml``) is valid and useful.
    """
    # Strip a UTF-8 BOM so hand-edited files from Windows round-trip cleanly.
    if text.startswith("﻿"):
        text = text[1:]
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise InvalidYamlError(f"{source}: {e}") from e
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise InvalidYamlError(
            f"{source}: top level must be a mapping "
            f"(e.g. `researcher: {{role: ..., goal: ...}}`), got {type(parsed).__name__}"
        )
    return parsed


# ---------------------------------------------------------------------------
# Agent parsing
# ---------------------------------------------------------------------------

# Fields we recognize on an agent entry. Everything else is warned + dropped.
# Kept in sync with `Agent`'s model_fields; the assertion below catches drift.
_AGENT_FIELDS: set[str] = {
    "role", "goal", "backstory", "llm", "tools", "max_iter", "max_rpm",
    "verbose", "allow_delegation", "reasoning", "multimodal",
    "respect_context_window", "max_retry_limit", "inject_date", "date_format",
}
# Sanity check at import time so an added Agent field never quietly gets
# dropped by the importer. The 'name' field is set from the YAML key, not
# from the body.
assert _AGENT_FIELDS <= set(Agent.model_fields) - {"name"}, (
    "Agent gained a field that _AGENT_FIELDS doesn't know about"
)


def _parse_agent(name: str, body: Any, warnings: list[str]) -> Agent:
    """Turn ``agents.yaml``'s ``name: {...}`` entry into an `Agent` model."""
    if not isinstance(body, dict):
        raise SchemaError(f"agents.yaml: agent {name!r} body must be a mapping")

    fields: dict[str, Any] = {"name": name}
    for key, value in body.items():
        if key in _AGENT_FIELDS:
            fields[key] = value
        else:
            warnings.append(f"agents.yaml: agent {name!r} — ignoring unknown field {key!r}")

    # Normalize `tools`: any of `["x", None, ""]` or a bare string should be a
    # clean list[str]. Reject non-string entries with a clear message.
    if "tools" in fields:
        fields["tools"] = _coerce_str_list(
            fields["tools"], where=f"agents.yaml agent {name!r}, field 'tools'"
        )

    # Pydantic gives us free type validation for the rest; wrap for context.
    try:
        return Agent(**fields)
    except ValidationError as e:
        raise SchemaError(f"agents.yaml: agent {name!r} — {e}") from e


# ---------------------------------------------------------------------------
# Task parsing
# ---------------------------------------------------------------------------

_TASK_FIELDS: set[str] = {
    "description", "expected_output", "agent", "tools", "context",
    "async_execution", "human_input", "markdown", "output_file",
}
assert _TASK_FIELDS <= set(Task.model_fields) - {"name"}, (
    "Task gained a field that _TASK_FIELDS doesn't know about"
)


def _parse_task(
    name: str, body: Any, agent_names: set[str], warnings: list[str]
) -> Task:
    """Turn ``tasks.yaml``'s ``name: {...}`` entry into a `Task` model."""
    if not isinstance(body, dict):
        raise SchemaError(f"tasks.yaml: task {name!r} body must be a mapping")

    fields: dict[str, Any] = {"name": name}
    for key, value in body.items():
        if key in _TASK_FIELDS:
            fields[key] = value
        else:
            warnings.append(f"tasks.yaml: task {name!r} — ignoring unknown field {key!r}")

    if "tools" in fields:
        fields["tools"] = _coerce_str_list(
            fields["tools"], where=f"tasks.yaml task {name!r}, field 'tools'"
        )
    if "context" in fields:
        fields["context"] = _coerce_str_list(
            fields["context"], where=f"tasks.yaml task {name!r}, field 'context'"
        )

    # Warn (don't fail) on an agent reference that doesn't resolve. The
    # existing validator flags this before export.
    agent_ref = fields.get("agent")
    if agent_ref and agent_ref not in agent_names:
        warnings.append(
            f"tasks.yaml: task {name!r} references unknown agent {agent_ref!r} "
            "— check the Agents tab after import."
        )

    try:
        return Task(**fields)
    except ValidationError as e:
        raise SchemaError(f"tasks.yaml: task {name!r} — {e}") from e


# ---------------------------------------------------------------------------
# Tool synthesis
# ---------------------------------------------------------------------------

def _synthesize_tool_configs(
    agents: list[Agent], tasks: list[Task], warnings: list[str]
) -> list[ToolConfig]:
    """Materialize one `ToolConfig` per unique tool name referenced anywhere.

    CrewAI's YAML references tools by name only — the actual class is
    resolved at `crew.py` import time. We can't recover the kind from the
    YAML alone, so every synthesized `ToolConfig` carries
    :data:`UNKNOWN_TOOL_KIND` and a warning is added. The Tools tab shows
    a picker for each; :mod:`app.validate` blocks export until they're
    resolved.
    """
    referenced: list[str] = []
    seen: set[str] = set()
    for a in agents:
        for name in a.tools:
            if name and name not in seen:
                seen.add(name)
                referenced.append(name)
    for t in tasks:
        for name in t.tools:
            if name and name not in seen:
                seen.add(name)
                referenced.append(name)

    if not referenced:
        return []

    warnings.append(
        f"{len(referenced)} tool(s) referenced by agents/tasks — "
        "open the Tools tab and choose a type for each before exporting."
    )
    return [ToolConfig(name=n, kind=UNKNOWN_TOOL_KIND) for n in referenced]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_str_list(value: Any, *, where: str) -> list[str]:
    """Normalize ``value`` into a list of non-empty strings.

    Tolerates:
    - `null` / missing → empty list.
    - A bare string → single-element list (CrewAI tutorials sometimes do this).
    - A list with `None`/empty strings → those are dropped.

    Rejects non-string list items with a clear pointer to the offending field.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        raise SchemaError(f"{where}: expected a list of strings, got {type(value).__name__}")
    out: list[str] = []
    for item in value:
        if item is None or item == "":
            continue
        if not isinstance(item, str):
            raise SchemaError(f"{where}: list entries must be strings, got {type(item).__name__}")
        out.append(item)
    return out
