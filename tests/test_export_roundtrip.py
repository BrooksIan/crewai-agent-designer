"""End-to-end roundtrip: design → zip → extract → parse everything.

Does *not* run the crew (that needs a real LLM key), but does check that the
downloaded artifact is internally consistent and would import cleanly.
"""

from __future__ import annotations

import ast
import io
import zipfile
from pathlib import Path

import yaml

from app import generate
from app.models import Design


def test_roundtrip_extracts_and_parses(simple_design: Design, tmp_path: Path) -> None:
    data = generate.to_zip(simple_design)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(tmp_path)

    root = tmp_path / "researchcrew"
    assert root.is_dir()

    # YAML files must parse and contain the expected top-level keys.
    agents_doc = yaml.safe_load((root / "config" / "agents.yaml").read_text())
    tasks_doc = yaml.safe_load((root / "config" / "tasks.yaml").read_text())
    assert set(agents_doc.keys()) == {"researcher", "writer"}
    assert set(tasks_doc.keys()) == {"research", "write_brief"}

    # crew.py must parse and define ResearchCrew + expected methods.
    crew_src = (root / "crew.py").read_text()
    tree = ast.parse(crew_src)
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    assert len(classes) == 1
    assert classes[0].name == "ResearchCrew"

    method_names = {
        n.name
        for cls in classes
        for n in cls.body
        if isinstance(n, ast.FunctionDef)
    }
    assert {"researcher", "writer", "research", "write_brief", "crew"} <= method_names

    # requirements.txt and README.md are non-empty.
    assert (root / "requirements.txt").stat().st_size > 0
    assert "ResearchCrew" in (root / "README.md").read_text()


def test_task_references_agent_by_name(simple_design: Design) -> None:
    """After export, tasks.yaml should reference the agent by its name (the
    same string used as the @agent method name in crew.py)."""
    text = generate.to_tasks_yaml(simple_design)
    doc = yaml.safe_load(text)
    assert doc["research"]["agent"] == "researcher"
    assert doc["write_brief"]["agent"] == "writer"
