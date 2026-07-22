"""Tests for Cloudera Agent Studio zip import (`app.import_cas`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import cas, cas_workflow, import_cas, validate
from app.models import Agent, Crew, Design, ToolConfig
from app.tools_catalog import CUSTOM_TOOL_KIND, is_custom

FIXTURE = Path(__file__).parent / "fixtures" / "cas" / "AEAD-v2-template.zip"


@pytest.fixture
def aead_bytes() -> bytes:
    assert FIXTURE.is_file(), f"missing fixture {FIXTURE}"
    return FIXTURE.read_bytes()


def test_from_zip_aead_imports_agents_tools_tasks(aead_bytes: bytes) -> None:
    result = import_cas.from_zip(aead_bytes)
    design = result.design

    assert len(design.agents) == 3
    assert "Document_Processing_Manager" not in design.agent_names()
    assert any("manager" in w.lower() for w in result.warnings)

    assert len(design.tools) == 4
    assert all(is_custom(t.kind) for t in design.tools)
    assert all(t.python_code and t.python_code.strip() for t in design.tools)

    assert len(design.tasks) == 1
    assert design.tasks[0].name == "task_1"
    assert design.tasks[0].agent is None

    assert design.crew.process == "hierarchical"
    assert design.crew.name == "AEAD_v2"

    assert validate.validate(design, target="cas_workflow") == []


def test_from_zip_rejects_bad_bytes() -> None:
    with pytest.raises(import_cas.InvalidZipError):
        import_cas.from_zip(b"not-a-zip")


def test_from_zip_rejects_zip_without_workflow_json() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "nope")
    with pytest.raises(import_cas.InvalidZipError, match="workflow_template"):
        import_cas.from_zip(buf.getvalue())


def test_custom_tool_blocks_crewai_validate_allows_cas() -> None:
    design = Design(
        agents=[Agent(name="a", role="r", goal="g", backstory="b", tools=["ocr"])],
        tools=[
            ToolConfig(
                name="ocr",
                kind=CUSTOM_TOOL_KIND,
                python_code="def run_tool():\n    return 1\n",
                requirements="",
            )
        ],
        crew=Crew(name="C", process="hierarchical", manager_llm="gpt-4o"),
    )
    cas_errors = validate.validate(design, target="cas_workflow")
    assert cas_errors == []

    crewai_errors = validate.validate(design, target="crewai")
    assert any("CustomTool" in e.message for e in crewai_errors)


def test_empty_custom_tool_code_blocks_cas_export() -> None:
    design = Design(
        agents=[Agent(name="a", role="r", goal="g", backstory="b")],
        tools=[ToolConfig(name="x", kind=CUSTOM_TOOL_KIND, python_code="")],
        crew=Crew(name="C"),
    )
    errors = validate.validate(design, target="cas_workflow")
    assert any("python_code" in e.where for e in errors)


def test_cas_tool_dir_passthrough_custom_source() -> None:
    source = "# custom tool\ndef run_tool():\n    return 'ok'\n"
    reqs = "pydantic>=2\n"
    tool = ToolConfig(
        name="my_custom",
        kind=CUSTOM_TOOL_KIND,
        python_code=source,
        requirements=reqs,
    )
    files = cas.to_cas_tool_dir(tool)
    assert files["tool.py"] == source
    assert files["requirements.txt"] == reqs


def test_custom_tool_scaffold_matches_cas_contract() -> None:
    source = cas.custom_tool_scaffold("my_tool")
    reqs = cas.custom_tool_requirements_scaffold()
    for needle in (
        "class UserParameters",
        "class ToolParameters",
        "def run_tool",
        "OUTPUT_KEY",
        '--user-params',
        '--tool-params',
        "my_tool",
    ):
        assert needle in source
    assert "pydantic" in reqs
    assert reqs.lstrip().startswith("# https://pip.pypa.io/")


def test_round_trip_preserves_custom_tool_source(aead_bytes: bytes) -> None:
    original = import_cas.from_zip(aead_bytes).design
    exported = cas_workflow.to_cas_workflow_zip(original)
    again = import_cas.from_zip(exported).design

    by_name_orig = {t.name: t for t in original.tools}
    by_name_again = {t.name: t for t in again.tools}
    assert set(by_name_orig) == set(by_name_again)
    for name, tool in by_name_orig.items():
        assert by_name_again[name].python_code == tool.python_code
        assert by_name_again[name].requirements == tool.requirements

    # Agent ↔ tool wiring survives by designer name.
    orig_links = {a.name: set(a.tools) for a in original.agents}
    again_links = {a.name: set(a.tools) for a in again.agents}
    assert orig_links == again_links
