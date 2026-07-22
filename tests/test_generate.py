"""Tests for `app.generate` — YAML rendering, crew.py template, zip layout."""

from __future__ import annotations

import ast
import io
import zipfile

import pytest
import yaml

from app import generate
from app.models import Design


def test_agents_yaml_has_required_fields(simple_design: Design) -> None:
    text = generate.to_agents_yaml(simple_design)
    doc = yaml.safe_load(text)
    assert set(doc.keys()) == {"researcher", "writer"}
    for name, agent in doc.items():
        assert "role" in agent, f"{name} missing role"
        assert "goal" in agent, f"{name} missing goal"
        assert "backstory" in agent, f"{name} missing backstory"


def test_agents_yaml_omits_default_optionals(simple_design: Design) -> None:
    """Defaults shouldn't clutter the file."""
    text = generate.to_agents_yaml(simple_design)
    doc = yaml.safe_load(text)
    # researcher uses defaults — shouldn't emit max_iter=20, verbose=False, etc.
    assert "max_iter" not in doc["researcher"]
    assert "verbose" not in doc["researcher"]
    # writer sets verbose=True — should appear.
    assert doc["writer"]["verbose"] is True


def test_agents_yaml_prose_is_folded(simple_design: Design) -> None:
    """Multi-line prose should render as folded block scalars, not inline."""
    text = generate.to_agents_yaml(simple_design)
    # Folded block scalar leader is ">"
    assert "role: >" in text or "role: >-" in text


def test_tasks_yaml_respects_task_order(simple_design: Design) -> None:
    simple_design.crew.task_order = ["write_brief", "research"]
    text = generate.to_tasks_yaml(simple_design)
    # yaml.safe_load doesn't preserve order in the returned dict, so check the
    # raw text order.
    assert text.index("write_brief:") < text.index("research:")


def test_tasks_yaml_default_order_is_declaration(simple_design: Design) -> None:
    text = generate.to_tasks_yaml(simple_design)
    assert text.index("research:") < text.index("write_brief:")


def test_crew_py_parses_as_python(simple_design: Design) -> None:
    code = generate.to_crew_py(simple_design)
    tree = ast.parse(code)  # would raise SyntaxError otherwise
    class_names = [
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    ]
    assert "ResearchCrew" in class_names


def test_crew_py_defines_method_per_agent_and_task(simple_design: Design) -> None:
    code = generate.to_crew_py(simple_design)
    tree = ast.parse(code)
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "researcher" in methods
    assert "writer" in methods
    assert "research" in methods
    assert "write_brief" in methods
    assert "crew" in methods


def test_crew_py_imports_used_tools(simple_design: Design) -> None:
    code = generate.to_crew_py(simple_design)
    assert "from crewai_tools import SerperDevTool" in code


def test_crew_py_skips_unused_tool_imports(simple_design: Design) -> None:
    """A declared but unused tool shouldn't be imported into crew.py."""
    from app.models import ToolConfig

    simple_design.tools.append(ToolConfig(name="unused", kind="WikipediaTools"))
    code = generate.to_crew_py(simple_design)
    assert "WikipediaTools" not in code


def test_crew_py_hierarchical_includes_manager_llm(hierarchical_design: Design) -> None:
    code = generate.to_crew_py(hierarchical_design)
    assert 'manager_llm="gpt-4o"' in code
    assert "Process.hierarchical" in code


def test_zip_contains_expected_layout(simple_design: Design) -> None:
    data = generate.to_zip(simple_design)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
    root = "researchcrew"
    assert f"{root}/config/agents.yaml" in names
    assert f"{root}/config/tasks.yaml" in names
    assert f"{root}/crew.py" in names
    assert f"{root}/requirements.txt" in names
    assert f"{root}/README.md" in names


def test_zip_contents_compile(simple_design: Design) -> None:
    """The exported crew.py must parse as valid Python."""
    data = generate.to_zip(simple_design)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        code = zf.read("researchcrew/crew.py").decode()
    ast.parse(code)


def test_folded_representer_handles_multiline(simple_design: Design) -> None:
    """A long backstory should still round-trip through YAML cleanly."""
    simple_design.agents[0].backstory = "Line one.\nLine two.\nLine three."
    text = generate.to_agents_yaml(simple_design)
    parsed = yaml.safe_load(text)
    # Folded scalar collapses newlines into spaces — that's the format CrewAI
    # expects. Content should still be present.
    assert "Line one" in parsed["researcher"]["backstory"]
    assert "Line three" in parsed["researcher"]["backstory"]
