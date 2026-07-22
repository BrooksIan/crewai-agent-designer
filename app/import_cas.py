"""Import a Cloudera Agent Studio workflow zip into a `Design`.

Pure module — the Streamlit sidebar calls :func:`from_zip` and surfaces
:class:`ImportResult` warnings the same way YAML import does.

Expected zip layout (CAS template export)::

    workflow_template.json
    studio-data/tool_templates/<slug>_<id>/{tool.py, requirements.txt}

Compatibility notes:

- Round-tripping our own :mod:`app.cas_workflow` output is the acceptance
  criterion. Arbitrary CAS templates (e.g. AEAD) are best-effort: agents,
  tasks, and custom tool source are preserved; manager persona, MCP, and
  icons are dropped with warnings.
- Tool ``name`` values are coerced to unique Python identifiers so they
  can be referenced from agents and survive designer validation.
- CAS task templates often omit ``name`` and never carry an agent ref —
  we synthesize ``task_N`` names and leave ``agent=None``.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from keyword import iskeyword
from typing import Any

from pydantic import ValidationError

from .models import Agent, Crew, Design, Task, ToolConfig
from .tools_catalog import CUSTOM_TOOL_KIND


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ImportError(Exception):
    """Base class for every error raised during CAS zip import."""


class InvalidZipError(ImportError):
    """Raised when the bytes aren't a zip or lack ``workflow_template.json``."""


class SchemaError(ImportError):
    """Raised when the JSON shape is present but unusable."""


@dataclass
class ImportResult:
    """Return value of :func:`from_zip`."""

    design: Design
    warnings: list[str] = field(default_factory=list)


def from_zip(data: bytes) -> ImportResult:
    """Parse a CAS workflow zip and assemble a `Design`.

    Fatal errors raise a subclass of :class:`ImportError`. Non-fatal
    projections (dropped manager, missing tool files, ignored MCP) are
    collected on :attr:`ImportResult.warnings`.
    """
    warnings: list[str] = []
    members = _open_zip_members(data)
    raw = _read_workflow_json(members)
    doc = _parse_workflow_doc(raw)

    workflow = doc["workflow_template"]
    tool_templates = doc.get("tool_templates") or []
    agent_templates = doc.get("agent_templates") or []
    task_templates = doc.get("task_templates") or []
    mcp_templates = doc.get("mcp_templates") or []

    if mcp_templates:
        warnings.append(
            f"{len(mcp_templates)} MCP template(s) were ignored — "
            "the designer does not model MCP servers yet."
        )

    tools, tool_id_to_name = _parse_tools(tool_templates, members, warnings)
    agents = _parse_agents(agent_templates, workflow, tool_id_to_name, warnings)
    tasks = _parse_tasks(task_templates, warnings)
    crew = _parse_crew(workflow, warnings)

    design = Design(agents=agents, tasks=tasks, tools=tools, crew=crew)
    return ImportResult(design=design, warnings=warnings)


# ---------------------------------------------------------------------------
# Zip / JSON loading
# ---------------------------------------------------------------------------

def _open_zip_members(data: bytes) -> dict[str, bytes]:
    """Return ``{normalized_path: bytes}`` for every file in the zip."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise InvalidZipError(f"not a valid zip archive: {e}") from e

    members: dict[str, bytes] = {}
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Normalize: strip leading ./ and collapse separators.
            name = info.filename.replace("\\", "/").lstrip("./")
            members[name] = zf.read(info)
    return members


def _read_workflow_json(members: dict[str, bytes]) -> bytes:
    """Find ``workflow_template.json`` at the zip root (or one nesting level)."""
    candidates = [
        "workflow_template.json",
        # Some CAS downloads wrap the template in a single top-level folder.
    ]
    for path in members:
        if path == "workflow_template.json" or path.endswith(
            "/workflow_template.json"
        ):
            # Prefer the shallowest match.
            candidates.append(path)

    # Deduplicate while preferring shortest path.
    seen: set[str] = set()
    ordered: list[str] = []
    for path in sorted(set(candidates), key=lambda p: (p.count("/"), p)):
        if path in members and path not in seen:
            seen.add(path)
            ordered.append(path)

    if not ordered:
        raise InvalidZipError(
            "zip is missing workflow_template.json "
            "(expected at the archive root)"
        )
    return members[ordered[0]]


def _parse_workflow_doc(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise InvalidZipError(f"workflow_template.json is not UTF-8: {e}") from e
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise InvalidZipError(f"workflow_template.json is not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise SchemaError("workflow_template.json: top level must be an object")
    if "workflow_template" not in doc or not isinstance(doc["workflow_template"], dict):
        raise SchemaError(
            "workflow_template.json: missing or invalid 'workflow_template' object"
        )
    for key in ("agent_templates", "task_templates", "tool_templates"):
        if key in doc and doc[key] is not None and not isinstance(doc[key], list):
            raise SchemaError(f"workflow_template.json: {key!r} must be a list")
    return doc


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _parse_tools(
    tool_templates: list[Any],
    members: dict[str, bytes],
    warnings: list[str],
) -> tuple[list[ToolConfig], dict[str, str]]:
    """Return tools and a UUID→designer-name map."""
    tools: list[ToolConfig] = []
    id_to_name: dict[str, str] = {}
    used_names: set[str] = set()

    for i, entry in enumerate(tool_templates):
        if not isinstance(entry, dict):
            warnings.append(f"tool_templates[{i}]: skipped — not an object")
            continue

        tool_id = str(entry.get("id") or "")
        raw_name = str(entry.get("name") or "").strip()
        folder = str(entry.get("source_folder_path") or "").strip().rstrip("/")
        folder_slug = folder.rsplit("/", 1)[-1] if folder else ""

        base = _to_identifier(raw_name, fallback=folder_slug or f"tool_{i + 1}")
        name = _unique_name(base, used_names, disambiguator=folder_slug or tool_id[:8])
        used_names.add(name)

        code_name = entry.get("python_code_file_name") or "tool.py"
        req_name = entry.get("python_requirements_file_name") or "requirements.txt"

        python_code = _read_member_text(members, folder, code_name)
        requirements = _read_member_text(members, folder, req_name)

        if python_code is None:
            warnings.append(
                f"tool {name!r}: missing {folder}/{code_name} — "
                "imported as CustomTool with empty source; paste code on the Tools tab."
            )
            python_code = ""
        if requirements is None:
            warnings.append(
                f"tool {name!r}: missing {folder}/{req_name} — using empty requirements."
            )
            requirements = ""

        try:
            tool = ToolConfig(
                name=name,
                kind=CUSTOM_TOOL_KIND,
                python_code=python_code,
                requirements=requirements,
            )
        except ValidationError as e:
            raise SchemaError(f"tool_templates[{i}] ({name!r}): {e}") from e

        tools.append(tool)
        if tool_id:
            id_to_name[tool_id] = name

    if any(
        isinstance(e, dict) and e.get("tool_image_path") for e in tool_templates
    ):
        warnings.append("tool icon paths ignored.")

    return tools, id_to_name


def _read_member_text(
    members: dict[str, bytes], folder: str, filename: str
) -> str | None:
    """Read a text file under ``folder``, trying a few path normalizations."""
    if not folder:
        return None
    folder = folder.replace("\\", "/").strip("/")
    leaf = folder.rsplit("/", 1)[-1]
    candidates = [
        f"{folder}/{filename}",
        # Some zips omit the studio-data prefix inconsistently.
        f"studio-data/tool_templates/{leaf}/{filename}",
    ]
    for path in candidates:
        if path in members:
            return members[path].decode("utf-8", errors="replace")
    # Last resort: match by trailing ``/<leaf>/<filename>``.
    suffix = f"/{leaf}/{filename}"
    for path, raw in members.items():
        if path.endswith(suffix):
            return raw.decode("utf-8", errors="replace")
    return None


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

def _parse_agents(
    agent_templates: list[Any],
    workflow: dict[str, Any],
    tool_id_to_name: dict[str, str],
    warnings: list[str],
) -> list[Agent]:
    manager_id = workflow.get("manager_agent_template_id")
    used_names: set[str] = set()
    agents: list[Agent] = []

    for i, entry in enumerate(agent_templates):
        if not isinstance(entry, dict):
            warnings.append(f"agent_templates[{i}]: skipped — not an object")
            continue

        agent_id = entry.get("id")
        if manager_id and agent_id == manager_id:
            mgr_name = entry.get("name") or "manager"
            warnings.append(
                f"manager agent {mgr_name!r} was not imported as a designer "
                "agent — CAS re-export synthesizes a Workflow Manager."
            )
            continue

        raw_name = str(entry.get("name") or f"agent_{i + 1}").strip()
        name = _unique_name(
            _to_identifier(raw_name, fallback=f"agent_{i + 1}"),
            used_names,
            disambiguator=str(agent_id or "")[:8],
        )
        used_names.add(name)

        tool_ids = entry.get("tool_template_ids") or []
        tool_names: list[str] = []
        if isinstance(tool_ids, list):
            for tid in tool_ids:
                mapped = tool_id_to_name.get(str(tid))
                if mapped:
                    tool_names.append(mapped)
                else:
                    warnings.append(
                        f"agent {name!r}: unknown tool_template_id {tid!r} — skipped."
                    )

        max_iter = entry.get("max_iter", 20)
        if max_iter == 0:
            warnings.append(
                f"agent {name!r}: max_iter was 0 — coerced to 20 "
                "(CrewAI default)."
            )
            max_iter = 20

        # Prefer explicit role; CAS often duplicates name into role/description.
        role = str(entry.get("role") or entry.get("description") or raw_name)
        goal = str(entry.get("goal") or "")
        backstory = str(entry.get("backstory") or "")

        try:
            agents.append(
                Agent(
                    name=name,
                    role=role,
                    goal=goal,
                    backstory=backstory,
                    tools=tool_names,
                    max_iter=int(max_iter) if max_iter is not None else 20,
                    verbose=bool(entry.get("verbose", False)),
                    allow_delegation=bool(entry.get("allow_delegation", False)),
                )
            )
        except (ValidationError, TypeError, ValueError) as e:
            raise SchemaError(f"agent_templates[{i}] ({name!r}): {e}") from e

    if any(
        isinstance(e, dict)
        and (
            e.get("temperature") not in (None, "")
            or e.get("agent_image_path")
            or e.get("cache") is not None
        )
        for e in agent_templates
        if not (manager_id and isinstance(e, dict) and e.get("id") == manager_id)
    ):
        warnings.append(
            "agent temperature / cache / icon fields ignored "
            "(not modeled in the designer)."
        )

    return agents


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def _parse_tasks(task_templates: list[Any], warnings: list[str]) -> list[Task]:
    tasks: list[Task] = []
    used_names: set[str] = set()

    for i, entry in enumerate(task_templates):
        if not isinstance(entry, dict):
            warnings.append(f"task_templates[{i}]: skipped — not an object")
            continue

        raw_name = str(entry.get("name") or "").strip()
        name = _unique_name(
            _to_identifier(raw_name, fallback=f"task_{i + 1}"),
            used_names,
            disambiguator=str(entry.get("id") or "")[:8],
        )
        used_names.add(name)

        description = str(entry.get("description") or "")
        expected_output = str(entry.get("expected_output") or "")
        if not description:
            warnings.append(f"task {name!r}: empty description.")
        if not expected_output:
            warnings.append(f"task {name!r}: empty expected_output.")

        warnings.append(
            f"task {name!r}: no agent assignment in CAS schema — "
            "assign an agent on the Tasks tab."
        )

        try:
            tasks.append(
                Task(
                    name=name,
                    description=description or "(imported from CAS — fill in)",
                    expected_output=expected_output or "(imported from CAS — fill in)",
                    agent=None,
                )
            )
        except ValidationError as e:
            raise SchemaError(f"task_templates[{i}] ({name!r}): {e}") from e

    return tasks


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[^0-9A-Za-z_]+")


def _parse_crew(workflow: dict[str, Any], warnings: list[str]) -> Crew:
    raw_name = str(workflow.get("name") or "ImportedCrew").strip()
    name = _to_identifier(raw_name, fallback="ImportedCrew")
    # Class names should be CapWords-ish; ensure it isn't a keyword.
    if iskeyword(name):
        name = f"{name}_crew"
    if name[0].isdigit():
        name = f"Crew_{name}"

    process_raw = str(workflow.get("process") or "sequential").lower()
    has_manager = bool(workflow.get("manager_agent_template_id"))
    if process_raw == "hierarchical" or has_manager:
        process = "hierarchical"
    else:
        process = "sequential"

    if name != raw_name:
        warnings.append(
            f"crew name {raw_name!r} sanitized to valid identifier {name!r}."
        )

    ignored_fields = [
        f
        for f in (
            "is_conversational",
            "smart_workflow",
            "planning",
            "pre_packaged",
            "use_default_manager",
        )
        if f in workflow
    ]
    if ignored_fields:
        warnings.append(
            "workflow CAS-only field(s) ignored: " + ", ".join(ignored_fields) + "."
        )

    return Crew(name=name, process=process)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

def _to_identifier(raw: str, *, fallback: str) -> str:
    """Coerce ``raw`` into a Python identifier (lowercase snake preferred)."""
    s = _IDENT_RE.sub("_", raw).strip("_")
    if not s:
        s = _IDENT_RE.sub("_", fallback).strip("_") or "item"
    if s[0].isdigit():
        s = f"t_{s}"
    if not s.isidentifier() or iskeyword(s):
        s = f"{s}_tool" if not s.isidentifier() else f"{s}_"
        s = _IDENT_RE.sub("_", s).strip("_") or "item"
    return s


def _unique_name(base: str, used: set[str], *, disambiguator: str = "") -> str:
    """Return ``base`` or ``base_<suffix>`` until unique within ``used``."""
    if base not in used:
        return base
    suffix = _IDENT_RE.sub("_", disambiguator).strip("_") or "dup"
    candidate = f"{base}_{suffix}"
    if candidate not in used:
        return candidate
    n = 2
    while f"{candidate}_{n}" in used:
        n += 1
    return f"{candidate}_{n}"
