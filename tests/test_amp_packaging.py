"""Guardrails for CML AMP packaging layout.

Ensures root ``.project-metadata.yaml`` points at real scripts and that
legacy ``deploy/`` packaging is gone.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_project_metadata_at_repo_root() -> None:
    meta_path = ROOT / ".project-metadata.yaml"
    assert meta_path.is_file()
    assert not (ROOT / "deploy" / ".project-metadata.yaml").exists()
    assert not (ROOT / "deploy").exists()


def test_amp_tasks_reference_existing_scripts() -> None:
    meta = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
    tasks = meta["tasks"]
    types = {t["type"] for t in tasks}
    assert "run_session" in types
    assert "start_application" in types
    for task in tasks:
        script = task.get("script")
        assert script, task
        assert (ROOT / script).is_file(), script
        if task["type"] == "start_application":
            assert task.get("kernel") == "python3"
            assert task.get("subdomain")


def test_launch_script_binds_cml_port() -> None:
    launch = (ROOT / "1_app-crewai-designer" / "launch_app.py").read_text()
    assert "CDSW_APP_PORT" in launch
    assert "streamlit run app/streamlit_app.py" in launch


def test_catalog_entry_present() -> None:
    catalog = yaml.safe_load((ROOT / "catalog-entry.yaml").read_text())
    entry = catalog["entries"][0]
    assert entry["title"]
    assert entry["git_url"]
    assert entry["image_path"]
    assert (ROOT / "assets" / "cover.png").is_file()
