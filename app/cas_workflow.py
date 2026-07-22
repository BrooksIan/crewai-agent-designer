"""Cloudera Agent Studio (CAS) — full workflow bundle export.

CAS accepts a zip containing a top-level ``workflow_template.json`` plus a
``studio-data/tool_templates/`` directory holding each tool as its own folder
(``tool.py`` + ``requirements.txt``, matching the shape produced by
`app.cas`). This module renders that bundle from a `Design`.

The workflow JSON schema is reverse-engineered from the reference at
``ClouderaAgentStudioeamples/workflow_template_5njz3ywr/workflow_template.json``.
Every field's presence, name, and casing there is treated as authoritative.

Fields in `Design` that don't have a home in the CAS schema
(`Task.context`, `Task.tools`, task ordering, `Crew.memory`, etc.) are
enumerated by :func:`warnings_for_dropped_fields` so the Export tab can
surface them to the user rather than silently dropping data.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import cas
from .models import Agent, Design, Task, ToolConfig
from .tools_catalog import by_kind, is_custom


# ---------------------------------------------------------------------------
# UUID generation
# ---------------------------------------------------------------------------

# Special-purpose names for the synthesized manager and task templates.
_MANAGER_NAME_KEY = "__manager__"
_TASK_NAME_KEY = "__conversational__"


def _new_workflow_id() -> str:
    """Mint a fresh workflow ID.

    Kept in its own function so tests can monkey-patch it for determinism.
    Callers of :func:`to_cas_workflow_json` / :func:`to_cas_workflow_zip`
    can also pass ``workflow_id=`` explicitly to bypass this — the same
    hook the test suite uses.

    We use ``uuid4`` here (not ``uuid5``) because a **new UUID per export
    is the correct behavior** for CAS imports: if the user imports the
    same crew twice, CAS should accept both as distinct workflow
    templates. Deterministic workflow IDs caused import collisions.
    """
    return str(uuid.uuid4())


def _child_uuid(workflow_id: str, kind: str, name: str) -> str:
    """Return a UUID for a child entity of ``workflow_id``, derived by
    hashing ``(workflow_id, kind, name)``.

    - **Within one export**, this is deterministic — same workflow_id +
      name → same child UUID. That's what cross-references need (an
      agent's `tool_template_ids` must resolve to the tool_template it's
      pointing at, and both come from the same workflow_id here).
    - **Across exports**, `_new_workflow_id()` picks a fresh
      `workflow_id`, so every child UUID also changes. No collisions.

    ``kind`` prevents intra-workflow name collisions across entity types
    (an agent and a tool with the same name still get different UUIDs).
    """
    namespace = uuid.UUID(workflow_id)
    return str(uuid.uuid5(namespace, f"{kind}:{name}"))


# ---------------------------------------------------------------------------
# Warnings — surface Design fields the CAS schema doesn't preserve
# ---------------------------------------------------------------------------

def warnings_for_dropped_fields(design: Design) -> list[str]:
    """Enumerate `Design` fields with no home in the CAS workflow schema.

    Returned strings are user-facing — short, action-oriented, and specific
    about which task/agent triggered each warning. The Export tab renders
    them as `st.warning`s before the download button so users can decide
    whether to keep the CrewAI export for full fidelity.
    """
    msgs: list[str] = []
    for t in design.tasks:
        if t.context:
            msgs.append(
                f"Task {t.name!r}: context dependencies {t.context} won't survive "
                "— CAS uses a single manager-orchestrated task."
            )
        if t.tools:
            msgs.append(
                f"Task {t.name!r}: task-level tool overrides {t.tools} won't survive "
                "— tools bind at the agent level in CAS."
            )
        if t.async_execution:
            msgs.append(f"Task {t.name!r}: async_execution has no CAS equivalent.")
        if t.human_input:
            msgs.append(f"Task {t.name!r}: human_input has no CAS equivalent.")
        if t.output_file:
            msgs.append(
                f"Task {t.name!r}: output_file {t.output_file!r} won't survive — "
                "CAS artifacts land in the session workspace."
            )
    if design.crew.memory:
        msgs.append("Crew memory is not exposed at crew level in CAS (agent-level cache only).")
    if design.crew.task_order:
        msgs.append(
            "Task execution order is preserved only through description prose — "
            "CAS's manager decides the actual order at runtime."
        )
    return msgs


# ---------------------------------------------------------------------------
# Workflow JSON construction
# ---------------------------------------------------------------------------

def _concatenated_task_description(design: Design) -> str:
    """Fold every task in the design into a single instruction block.

    CAS's example workflow uses one generic conversational task that reads
    ``Respond to the user's message: '{user_input}'. Conversation history:
    {context}.`` We keep the same wrapping so CAS's session runtime feeds
    ``user_input`` and ``context`` in the same way, but prepend the user's
    designed tasks so the manager has explicit instructions to work from.
    """
    if not design.tasks:
        # Preserve CAS's exact default when the user hasn't added tasks.
        return (
            "Respond to the user's message: '{user_input}'. "
            "Conversation history:\n{context}."
        )
    lines = [f"Workflow: {design.crew.name}.", "", "Tasks to perform, in order:"]
    for i, t in enumerate(design.tasks, start=1):
        agent_hint = f" (delegate to: {t.agent})" if t.agent else ""
        lines.append(f"{i}. {t.name}{agent_hint}: {t.description}")
        lines.append(f"   Expected output: {t.expected_output}")
    lines.append("")
    lines.append(
        "Respond to the user's message: '{user_input}'. "
        "Conversation history:\n{context}."
    )
    return "\n".join(lines)


def _concatenated_task_expected_output(design: Design) -> str:
    """The workflow-level expected_output rolls up per-task deliverables.

    CAS renders this next to the description at runtime, so we still want
    the user's intent visible even after we've collapsed the per-task list
    into a single task_template entry.
    """
    if not design.tasks:
        return "Provide a response that aligns with the conversation history."
    parts = [f"{t.name}: {t.expected_output}" for t in design.tasks]
    return "; ".join(parts)


def _agent_entry(agent: Agent, workflow_id: str, tool_uuid_by_name: dict[str, str]) -> dict[str, Any]:
    """Convert one `Agent` into a CAS ``agent_templates`` entry.

    Field set is the union of keys that appear on non-manager agents in
    real CAS templates (``avn5d431``, ``dbtp5e4r``, AEAD). We always emit
    ``mcp_template_ids: []`` — newer CAS exports include it, and an empty
    list is a safe no-op on older importers.
    """
    entry: dict[str, Any] = {
        "id": _child_uuid(workflow_id, "agent", agent.name),
        "workflow_template_id": workflow_id,
        "name": agent.name,
        "description": agent.role,
        "role": agent.role,
        "backstory": agent.backstory,
        "goal": agent.goal,
        "allow_delegation": agent.allow_delegation,
        "verbose": agent.verbose,
        "cache": True,  # CAS default; not exposed at Agent level in our model
        "temperature": 0.1,  # CAS default (some refs emit 0.10000000149011612 — that's a float32 artifact, functionally identical)
        "max_iter": agent.max_iter,
        "tool_template_ids": [],  # populated below if any tools are bound
        "mcp_template_ids": [],
        "pre_packaged": False,
        "agent_image_path": "",
    }
    if agent.tools:
        # Only reference tools that were declared in the design — validation
        # already ran, but be defensive: skip unknown names rather than
        # crash the export.
        entry["tool_template_ids"] = [
            tool_uuid_by_name[name]
            for name in agent.tools
            if name in tool_uuid_by_name
        ]
    return entry


def _manager_entry(workflow_id: str) -> dict[str, Any]:
    """Synthesize the manager agent CAS's hierarchical process expects.

    The manager's key-set is the **intersection** of the two authoritative
    CAS references. Fields that appeared on only one reference
    (``agent_image_path``, ``tool_template_ids``) are dropped: CAS's
    importer accepts a manager without them, and keeping them tied us to
    one specific reference's shape.
    """
    return {
        "id": _child_uuid(workflow_id, "agent", _MANAGER_NAME_KEY),
        "workflow_template_id": workflow_id,
        "name": "Workflow Manager",
        "description": "Autonomous manager for this workflow",
        "role": "Autonomous Workflow Manager",
        "backstory": (
            "You orchestrate the specialists on this workflow. If the user's request is "
            "purely conversational (greeting, thanks, small talk, follow-ups, "
            "clarifications, meta questions) and does not require tool use, respond "
            "directly based on the conversation history. Otherwise, delegate to the "
            "specialist agent best suited to the task and synthesize their outputs into "
            "a concise, evidence-backed final answer for the user."
        ),
        "goal": "Route each user request to the right specialist and return a coherent final answer.",
        "allow_delegation": True,
        "verbose": False,
        "cache": False,
        "temperature": 0.1,
        "max_iter": 0,
        "pre_packaged": False,
    }


def _tool_entry(
    tool: ToolConfig, workflow_id: str
) -> dict[str, Any]:
    """Convert one `ToolConfig` into a CAS ``tool_templates`` entry.

    ``source_folder_path`` must match the actual directory the zip writes,
    so both callers derive it from the same :func:`cas._slug_and_id` helper.
    """
    slug_and_id = cas._slug_and_id(tool.name)
    entry = by_kind(tool.kind)
    if entry is None:
        raise cas.CasExportError(f"Tool kind {tool.kind!r} is not in the catalog.")
    # Custom tools keep the designer name so CAS import can round-trip the
    # display label; catalog tools keep using the catalog label (CAS's
    # pre-built naming).
    display_name = tool.name if is_custom(tool.kind) else entry.label
    return {
        "id": _child_uuid(workflow_id, "tool", tool.name),
        "workflow_template_id": workflow_id,
        "name": display_name,
        "python_code_file_name": "tool.py",
        "python_requirements_file_name": "requirements.txt",
        "source_folder_path": f"studio-data/tool_templates/{slug_and_id}",
        "pre_built": False,
        # Empty string matches working CAS exports (AEAD). Pointing at a
        # PNG path without shipping the file caused CAS upload failures.
        "tool_image_path": "",
        "is_venv_tool": True,
    }


def _task_entry(design: Design, workflow_id: str) -> dict[str, Any]:
    """Build the single task_template entry the workflow references.

    Field set is the intersection of both authoritative CAS references
    (``id``, ``workflow_template_id``, ``description``, ``expected_output``).
    ``name`` appears on only one of them, so it's dropped — CAS's importer
    treats it as optional.
    """
    return {
        "id": _child_uuid(workflow_id, "task", _TASK_NAME_KEY),
        "workflow_template_id": workflow_id,
        "description": _concatenated_task_description(design),
        "expected_output": _concatenated_task_expected_output(design),
    }


def to_cas_workflow_json(design: Design, *, workflow_id: str | None = None) -> dict[str, Any]:
    """Build the workflow_template.json payload as a Python dict.

    Kept as a public entry point so tests and the Preview tab can consume
    the structured data directly without parsing JSON strings.

    ``workflow_id`` — when set, the export uses this exact string as the
    workflow's UUID (and every child UUID derives from it). Tests use
    this hook for byte-stable snapshots; UI callers leave it ``None`` so
    each export gets a fresh UUID from :func:`_new_workflow_id` and CAS
    accepts repeat imports of the same design without an ID collision.
    """
    if workflow_id is None:
        workflow_id = _new_workflow_id()

    # Tools first — every downstream agent entry needs the UUID map.
    tool_templates = [_tool_entry(t, workflow_id) for t in design.tools]
    tool_uuid_by_name = {
        t.name: _child_uuid(workflow_id, "tool", t.name) for t in design.tools
    }

    # Agents next — the manager is synthetic and lives alongside the user's.
    agent_templates = [_agent_entry(a, workflow_id, tool_uuid_by_name) for a in design.agents]
    manager = _manager_entry(workflow_id)
    agent_templates.append(manager)

    task_template = _task_entry(design, workflow_id)

    description = design.crew.name
    if design.tasks:
        description = f"{design.crew.name} — {len(design.tasks)} task(s) exported from the CrewAI Agent Designer."

    workflow = {
        "id": workflow_id,
        "name": design.crew.name,
        "description": description,
        # Always hierarchical: we synthesize a manager unconditionally so the
        # exported workflow runs the same way regardless of Design.process.
        # (Users who need sequential-style control keep the CrewAI export.)
        "process": "hierarchical",
        "agent_template_ids": [a["id"] for a in agent_templates if a["id"] != manager["id"]],
        "task_template_ids": [task_template["id"]],
        "manager_agent_template_id": manager["id"],
        "use_default_manager": False,
        "is_conversational": True,
        "pre_packaged": False,
        "smart_workflow": True,
        "planning": True,
    }

    return {
        "template_version": "0.0.1",
        "workflow_template": workflow,
        "agent_templates": agent_templates,
        "task_templates": [task_template],
        "tool_templates": tool_templates,
        "mcp_templates": [],
    }


# ---------------------------------------------------------------------------
# Zip bundling
# ---------------------------------------------------------------------------

def _jinja_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates" / "cas"
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _render_workflow_json(payload: dict[str, Any]) -> str:
    """Serialize the workflow payload with the same field ordering as CAS's
    reference example. We use Jinja rather than ``json.dumps`` because the
    field order in the reference is load-bearing for diff-friendliness and
    ``json.dumps`` doesn't guarantee anything about key order across
    Python versions."""
    template = _jinja_env().get_template("workflow_template.json.j2")
    workflow = payload["workflow_template"]
    rendered = template.render(
        workflow=workflow,
        agent_templates=payload["agent_templates"],
        tool_templates=payload["tool_templates"],
        task_templates=payload["task_templates"],
    )
    # Sanity: the template drives shape, but the caller relies on the output
    # being valid JSON. Round-trip through json to catch any Jinja glitches
    # and normalize whitespace to something that always parses cleanly.
    parsed = json.loads(rendered)
    return json.dumps(parsed, indent=2, sort_keys=False)


def to_cas_workflow_zip(design: Design, *, workflow_id: str | None = None) -> bytes:
    """Bundle the full CAS workflow upload as a zip.

    Layout matches working CAS template zips (e.g. AEAD):
    - ``workflow_template.json`` at the top level.
    - ``studio-data/tool_templates/<slug>_<hash>/{tool.py,requirements.txt}``
      per tool, delegating to `app.cas.to_cas_tool_dir` for file contents.
    No empty icon directories — ``tool_image_path`` / ``agent_image_path``
    are empty strings, same as CAS-authored exports without custom icons.

    ``workflow_id`` — see :func:`to_cas_workflow_json`. When ``None`` (the
    default), each export gets a fresh UUID so re-importing the same
    design into CAS doesn't collide with the previous import.

    Byte-stability: two calls with the **same** ``workflow_id`` produce
    byte-identical bytes (fixed zip timestamps + ``ZIP_STORED``). Without
    a workflow_id, the JSON differs by design.
    """
    payload = to_cas_workflow_json(design, workflow_id=workflow_id)
    json_text = _render_workflow_json(payload)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        _write_bytes(zf, "workflow_template.json", json_text.encode("utf-8"))

        # One directory per tool. Sorted so byte order is deterministic.
        for tool in sorted(design.tools, key=lambda t: t.name):
            dirname = cas._slug_and_id(tool.name)
            for filename, content in cas.to_cas_tool_dir(tool).items():
                _write_bytes(
                    zf,
                    f"studio-data/tool_templates/{dirname}/{filename}",
                    content.encode("utf-8"),
                )
    return buf.getvalue()


def _write_bytes(zf: zipfile.ZipFile, path: str, data: bytes) -> None:
    """Write a file to the zip with a fixed timestamp for determinism."""
    info = zipfile.ZipInfo(path)
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)
