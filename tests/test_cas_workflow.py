"""Tests for `app.cas_workflow` — full CAS workflow bundle export.

**Parity strategy.** Reference templates under ``tests/fixtures/cas/``
disagree on optional fields (``mcp_template_ids``, ``agent_image_path``,
``task_templates[].name``, etc.). Rather than pinning our output to one
specific reference, we assert that every field our generator emits is
**present in at least one** real template — the union of the fixtures.
That way the generator stays inside CAS's tolerated schema without
over-fitting to any single example.

Also covered: JSON validity, cross-reference integrity (no dangling
UUIDs), zip layout, byte-level determinism with a pinned ``workflow_id``,
freshness across re-exports, and dropped-field warnings.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest

from app import cas_workflow
from app.models import Agent, Crew, Design, Task, ToolConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# CAS / designer reference templates. Each is a valid workflow_template
# shape, so any field emitted by either is a field we're allowed to emit.
# Together they define the tolerated shape.
REFERENCE_PATHS = [
    Path(__file__).parent / "fixtures" / "cas" / "aead_v2_workflow_template.json",
    Path(__file__).parent / "fixtures" / "cas" / "designer_export_workflow_template.json",
]

# When we need a byte-stable export (snapshot tests, diff-friendly examples),
# pin the workflow_id. Any valid UUID works — this one is memorable.
_FIXED_WORKFLOW_ID = "00000000-0000-0000-0000-000000000042"


@pytest.fixture
def reference_payloads() -> list[dict]:
    """Load every real CAS workflow template we have on disk.

    Tests use the *union* of these as the schema envelope so they don't
    over-fit to any one authoring style.
    """
    payloads = []
    for p in REFERENCE_PATHS:
        if p.exists():
            payloads.append(json.loads(p.read_text()))
    if not payloads:
        pytest.skip("No CAS reference workflow_template.json files available")
    return payloads


def _union_keys(dicts: list[dict]) -> set[str]:
    """Union of top-level keys across a list of dicts."""
    out: set[str] = set()
    for d in dicts:
        out.update(d.keys())
    return out


def _find_manager(payload: dict) -> dict:
    """Manager agent within a workflow_template payload."""
    mgr_id = payload["workflow_template"]["manager_agent_template_id"]
    return next(a for a in payload["agent_templates"] if a["id"] == mgr_id)


def _find_non_manager(payload: dict) -> dict:
    """Any non-manager agent within a workflow_template payload."""
    mgr_id = payload["workflow_template"]["manager_agent_template_id"]
    return next(a for a in payload["agent_templates"] if a["id"] != mgr_id)


@pytest.fixture
def sample_design() -> Design:
    """A design with tools, agents, and multiple tasks — exercises every warning."""
    return Design(
        agents=[
            Agent(
                name="researcher",
                role="Senior Researcher",
                goal="Find things on the web.",
                backstory="Twenty years of experience skimming primary sources.",
                tools=["web_search"],
                verbose=True,
            ),
            Agent(
                name="writer",
                role="Technical Writer",
                goal="Turn research into prose.",
                backstory="Former newsroom editor.",
                allow_delegation=False,
            ),
        ],
        tasks=[
            Task(
                name="research",
                description="Investigate the topic.",
                expected_output="Bullet-pointed findings.",
                agent="researcher",
                tools=["web_search"],
            ),
            Task(
                name="write_brief",
                description="Compose the two-paragraph brief.",
                expected_output="Max 300 words.",
                agent="writer",
                context=["research"],
                output_file="brief.md",
            ),
        ],
        tools=[ToolConfig(name="web_search", kind="SerperDevTool")],
        crew=Crew(name="ResearchCrew", process="sequential"),
    )


# ---------------------------------------------------------------------------
# Structural parity vs real CAS templates
#
# Every field we emit must appear in AT LEAST ONE real CAS-authored template
# — that's the tolerated-shape check. We deliberately don't require every
# field from every reference: CAS's two authoritative examples disagree on
# some optionals (manager ``agent_image_path``, task ``name``, etc.) and
# staying within the union keeps exports robust.
# ---------------------------------------------------------------------------

def test_top_level_keys_match_reference(sample_design, reference_payloads) -> None:
    """Top-level shape is stable across both refs — this one is a hard equality."""
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    for ref in reference_payloads:
        assert set(gen.keys()) == set(ref.keys()), (
            f"top-level drift vs one reference: gen-only={set(gen)-set(ref)}, "
            f"ref-only={set(ref)-set(gen)}"
        )


def test_workflow_template_keys_match_reference(sample_design, reference_payloads) -> None:
    """workflow_template inner shape is also stable across both refs."""
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    gen_keys = set(gen["workflow_template"].keys())
    for ref in reference_payloads:
        assert gen_keys == set(ref["workflow_template"].keys()), (
            f"workflow_template drift: {gen_keys ^ set(ref['workflow_template'].keys())}"
        )


def test_non_manager_agent_fields_are_valid(sample_design, reference_payloads) -> None:
    """Every field on our non-manager agents appears in at least one real
    CAS reference (the union). Also every field required by BOTH refs is
    emitted (the intersection). Together this bounds us to CAS's tolerated
    shape without over-fitting to one example."""
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    gen_manager_id = gen["workflow_template"]["manager_agent_template_id"]
    gen_a = next(a for a in gen["agent_templates"] if a["id"] != gen_manager_id)

    ref_non_managers = [_find_non_manager(r) for r in reference_payloads]
    union = _union_keys(ref_non_managers)
    intersection = set.intersection(*[set(r.keys()) for r in ref_non_managers])

    gen_keys = set(gen_a.keys())
    stranger_fields = gen_keys - union
    missing_required = intersection - gen_keys

    assert not stranger_fields, (
        f"agent fields not present in any real CAS template: {stranger_fields}"
    )
    assert not missing_required, (
        f"agent fields present in every real CAS template but not emitted: {missing_required}"
    )


def test_manager_agent_fields_are_valid(sample_design, reference_payloads) -> None:
    """Same envelope check for the synthesized manager agent."""
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    gen_manager = next(
        a for a in gen["agent_templates"]
        if a["id"] == gen["workflow_template"]["manager_agent_template_id"]
    )
    ref_managers = [_find_manager(r) for r in reference_payloads]
    union = _union_keys(ref_managers)
    intersection = set.intersection(*[set(m.keys()) for m in ref_managers])

    gen_keys = set(gen_manager.keys())
    stranger_fields = gen_keys - union
    missing_required = intersection - gen_keys
    assert not stranger_fields, (
        f"manager fields not present in any real CAS template: {stranger_fields}"
    )
    assert not missing_required, (
        f"manager fields present in every real CAS template but not emitted: {missing_required}"
    )


def test_tool_template_keys_are_valid(sample_design, reference_payloads) -> None:
    """Envelope check for tool templates. Only exercised via a hardcoded
    expected key-set if neither reference includes tools (both current
    references happen to skip them)."""
    # Fallback matches the CAS tool_template schema we know from the
    # earlier reference and our own generator.
    _FALLBACK_TOOL_KEYS = {
        "id", "workflow_template_id", "name", "python_code_file_name",
        "python_requirements_file_name", "source_folder_path", "pre_built",
        "tool_image_path", "is_venv_tool",
    }
    ref_tools = [t for r in reference_payloads for t in r.get("tool_templates", [])]
    expected = set.union(*[set(t.keys()) for t in ref_tools]) if ref_tools else _FALLBACK_TOOL_KEYS

    gen = cas_workflow.to_cas_workflow_json(sample_design)
    assert set(gen["tool_templates"][0].keys()) <= expected, (
        f"tool template has stranger fields: {set(gen['tool_templates'][0].keys()) - expected}"
    )


def test_task_template_fields_are_valid(sample_design, reference_payloads) -> None:
    """Task template shape must be a subset of the union of both refs.
    ``name`` appears on only one CAS-authored reference — we deliberately
    don't emit it, so this test allows either shape."""
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    ref_tasks = [r["task_templates"][0] for r in reference_payloads]
    union = _union_keys(ref_tasks)
    intersection = set.intersection(*[set(t.keys()) for t in ref_tasks])

    gen_keys = set(gen["task_templates"][0].keys())
    stranger_fields = gen_keys - union
    missing_required = intersection - gen_keys

    assert not stranger_fields, (
        f"task fields not present in any real CAS template: {stranger_fields}"
    )
    assert not missing_required, (
        f"task fields present in every real CAS template but not emitted: {missing_required}"
    )


# ---------------------------------------------------------------------------
# JSON validity + cross-ref integrity
# ---------------------------------------------------------------------------

def test_zip_contents_parse_as_json(sample_design) -> None:
    """The bundled JSON must be a valid JSON document."""
    data = cas_workflow.to_cas_workflow_zip(sample_design)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        payload = json.loads(zf.read("workflow_template.json"))
    assert payload["workflow_template"]["name"] == "ResearchCrew"


def test_all_referenced_agent_ids_exist(sample_design) -> None:
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    defined = {a["id"] for a in gen["agent_templates"]}
    referenced = set(gen["workflow_template"]["agent_template_ids"])
    assert referenced <= defined, f"dangling agent refs: {referenced - defined}"


def test_manager_id_defined_but_not_in_agent_template_ids(sample_design) -> None:
    """The example splits the manager out — its UUID appears only in
    ``manager_agent_template_id`` and in the agents list, not in the
    ``agent_template_ids`` roster (which lists delegates)."""
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    manager_id = gen["workflow_template"]["manager_agent_template_id"]
    agent_ids = {a["id"] for a in gen["agent_templates"]}
    assert manager_id in agent_ids
    assert manager_id not in gen["workflow_template"]["agent_template_ids"]


def test_all_referenced_task_ids_exist(sample_design) -> None:
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    defined = {t["id"] for t in gen["task_templates"]}
    referenced = set(gen["workflow_template"]["task_template_ids"])
    assert referenced <= defined


def test_each_agent_tool_refs_resolve(sample_design) -> None:
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    tool_ids = {t["id"] for t in gen["tool_templates"]}
    for agent in gen["agent_templates"]:
        for tid in agent.get("tool_template_ids", []):
            assert tid in tool_ids, (
                f"agent {agent['name']!r} references undefined tool UUID {tid}"
            )


def test_uuids_are_valid(sample_design) -> None:
    """Every ``id`` field must parse as a real UUID — catches accidental
    string mangling in the generator."""
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    ids: list[str] = [gen["workflow_template"]["id"], gen["workflow_template"]["manager_agent_template_id"]]
    ids.extend(gen["workflow_template"]["agent_template_ids"])
    ids.extend(gen["workflow_template"]["task_template_ids"])
    for lst_key in ("agent_templates", "task_templates", "tool_templates"):
        ids.extend(item["id"] for item in gen[lst_key])
    for id_str in ids:
        uuid.UUID(id_str)


def test_uuids_are_deterministic_when_workflow_id_pinned(sample_design) -> None:
    """Passing an explicit ``workflow_id`` makes the whole export
    byte-stable. This is what snapshot tests and diff-friendly example
    fixtures use — every child UUID derives from workflow_id via uuid5."""
    a = cas_workflow.to_cas_workflow_json(sample_design, workflow_id=_FIXED_WORKFLOW_ID)
    b = cas_workflow.to_cas_workflow_json(sample_design, workflow_id=_FIXED_WORKFLOW_ID)
    assert a == b


def test_default_workflow_id_is_fresh_per_export(sample_design) -> None:
    """Without a pinned workflow_id, every export gets a new UUID. This
    is the fix for the CAS import collision — two imports of the same
    design should NOT share workflow IDs, or CAS rejects the second
    with a duplicate-id error."""
    a = cas_workflow.to_cas_workflow_json(sample_design)
    b = cas_workflow.to_cas_workflow_json(sample_design)
    assert a["workflow_template"]["id"] != b["workflow_template"]["id"]
    # And every child UUID also differs, since they're derived from workflow_id.
    a_agent_ids = {a_["id"] for a_ in a["agent_templates"]}
    b_agent_ids = {a_["id"] for a_ in b["agent_templates"]}
    assert not (a_agent_ids & b_agent_ids), "child UUIDs collided across exports"


def test_child_ids_derive_from_workflow_id(sample_design) -> None:
    """When workflow_id is pinned, changing it also changes every child ID
    (that's how deterministic-within-export + fresh-across-export coexists)."""
    a = cas_workflow.to_cas_workflow_json(sample_design, workflow_id=_FIXED_WORKFLOW_ID)
    other = "11111111-1111-1111-1111-111111111111"
    b = cas_workflow.to_cas_workflow_json(sample_design, workflow_id=other)
    a_ids = {a_["id"] for a_ in a["agent_templates"]}
    b_ids = {a_["id"] for a_ in b["agent_templates"]}
    assert not (a_ids & b_ids)


# ---------------------------------------------------------------------------
# Zip layout + determinism
# ---------------------------------------------------------------------------

def test_zip_layout_matches_cas_example(sample_design) -> None:
    data = cas_workflow.to_cas_workflow_zip(sample_design)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
    # Top-level JSON at the root.
    assert "workflow_template.json" in names
    # Tool sources only — no empty icon directory stubs (AEAD-style).
    assert not any("dynamic_assets" in n for n in names)
    # One tool directory with the CAS contract files.
    tool_dirs = {n.split("/")[-2] for n in names if n.startswith("studio-data/tool_templates/") and n.endswith("tool.py")}
    assert len(tool_dirs) == 1
    dir_name = tool_dirs.pop()
    assert f"studio-data/tool_templates/{dir_name}/tool.py" in names
    assert f"studio-data/tool_templates/{dir_name}/requirements.txt" in names


def test_tool_image_paths_do_not_dangle(sample_design) -> None:
    """CAS upload fails when ``tool_image_path`` points at a missing PNG.

    Working CAS templates (AEAD) use an empty string when no icon ships.
    """
    data = cas_workflow.to_cas_workflow_zip(sample_design)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        payload = json.loads(zf.read("workflow_template.json"))
    for tt in payload["tool_templates"]:
        path = tt.get("tool_image_path") or ""
        if path:
            assert path in names, (
                f"tool {tt['name']!r} tool_image_path={path!r} missing from zip"
            )
        else:
            assert path == ""


def test_non_manager_agents_emit_empty_mcp_template_ids(sample_design) -> None:
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    mid = gen["workflow_template"]["manager_agent_template_id"]
    for agent in gen["agent_templates"]:
        if agent["id"] == mid:
            assert "mcp_template_ids" not in agent
        else:
            assert agent.get("mcp_template_ids") == []


def test_tool_source_folder_path_matches_zip_layout(sample_design) -> None:
    """Every ``source_folder_path`` in the JSON must point at a directory
    the zip actually contains — a mismatch would 404 in CAS's importer."""
    data = cas_workflow.to_cas_workflow_zip(sample_design)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        payload = json.loads(zf.read("workflow_template.json"))
    for tt in payload["tool_templates"]:
        source = tt["source_folder_path"].rstrip("/") + "/"
        assert any(n.startswith(source) for n in names), (
            f"tool template points at {source!r} but nothing in zip matches"
        )


def test_zip_is_deterministic_with_pinned_workflow_id(sample_design) -> None:
    """Byte-identical zip output when the workflow_id is pinned — same
    contract as :func:`test_uuids_are_deterministic_when_workflow_id_pinned`
    but at the zip-bytes layer."""
    a = cas_workflow.to_cas_workflow_zip(sample_design, workflow_id=_FIXED_WORKFLOW_ID)
    b = cas_workflow.to_cas_workflow_zip(sample_design, workflow_id=_FIXED_WORKFLOW_ID)
    assert a == b


def test_zip_bytes_differ_when_workflow_id_is_fresh(sample_design) -> None:
    """Without pinning, two consecutive exports produce different bytes.
    This is the whole point of the fresh-UUID change — same-input CAS
    imports don't collide anymore."""
    a = cas_workflow.to_cas_workflow_zip(sample_design)
    b = cas_workflow.to_cas_workflow_zip(sample_design)
    assert a != b


# ---------------------------------------------------------------------------
# Dropped-field warnings
# ---------------------------------------------------------------------------

def test_warnings_flag_task_context(sample_design) -> None:
    msgs = cas_workflow.warnings_for_dropped_fields(sample_design)
    assert any("context dependencies" in m and "write_brief" in m for m in msgs)


def test_warnings_flag_task_tools(sample_design) -> None:
    msgs = cas_workflow.warnings_for_dropped_fields(sample_design)
    assert any("task-level tool overrides" in m and "research" in m for m in msgs)


def test_warnings_flag_output_file(sample_design) -> None:
    msgs = cas_workflow.warnings_for_dropped_fields(sample_design)
    assert any("output_file" in m and "brief.md" in m for m in msgs)


def test_no_warnings_for_clean_design() -> None:
    """A minimal design with no unsupported fields yields no warnings."""
    design = Design(
        agents=[
            Agent(
                name="a",
                role="R",
                goal="G",
                backstory="B",
            )
        ],
        tasks=[
            Task(name="only", description="D", expected_output="O", agent="a"),
        ],
        crew=Crew(name="C"),
    )
    assert cas_workflow.warnings_for_dropped_fields(design) == []


# ---------------------------------------------------------------------------
# Concatenated task description
# ---------------------------------------------------------------------------

def test_concatenated_task_description_contains_every_task(sample_design) -> None:
    gen = cas_workflow.to_cas_workflow_json(sample_design)
    desc = gen["task_templates"][0]["description"]
    assert "research" in desc
    assert "write_brief" in desc
    # The CAS runtime template is still there — CAS needs it to inject
    # `{user_input}` and `{context}` at session time.
    assert "{user_input}" in desc
    assert "{context}" in desc


def test_empty_tasks_falls_back_to_cas_default() -> None:
    """When the user hasn't defined any tasks, the exported workflow keeps
    CAS's default conversational shape so it still runs."""
    design = Design(crew=Crew(name="EmptyCrew"))
    gen = cas_workflow.to_cas_workflow_json(design)
    desc = gen["task_templates"][0]["description"]
    assert desc == (
        "Respond to the user's message: '{user_input}'. "
        "Conversation history:\n{context}."
    )
