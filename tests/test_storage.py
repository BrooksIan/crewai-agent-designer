"""Tests for design CRUD in `app.storage`."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app import storage
from app.models import Agent, Design


@pytest.fixture
def temp_storage():
    """Redirect design/workplace dirs into a temp folder for isolation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        with patch.object(storage, "DESIGNS_DIR", root / "designs"):
            with patch.object(storage, "WORKPLACES_DIR", root / "workplaces"):
                yield root


def test_save_load_roundtrip(temp_storage, simple_design: Design) -> None:
    storage.save("research", simple_design)
    loaded = storage.load("research")
    assert loaded.crew.name == simple_design.crew.name
    assert loaded.agent_names() == simple_design.agent_names()


def test_list_designs_sorted(temp_storage) -> None:
    storage.save("zeta", Design())
    storage.save("alpha", Design())
    assert storage.list_designs() == ["alpha", "zeta"]


def test_delete_design(temp_storage) -> None:
    storage.save("gone", Design())
    storage.delete("gone")
    assert storage.list_designs() == []


def test_load_missing_raises(temp_storage) -> None:
    with pytest.raises(storage.StorageError, match="not found"):
        storage.load("missing")


def test_invalid_name_rejected(temp_storage) -> None:
    with pytest.raises(storage.StorageError, match="Invalid design name"):
        storage.save("../escape", Design())


def test_corrupt_json_includes_path(temp_storage) -> None:
    storage.save("broken", Design())
    path = storage._path_for("broken")
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(storage.StorageError, match=str(path)):
        storage.load("broken")


def test_schema_invalid_json_includes_path(temp_storage) -> None:
    storage._ensure_dir()
    path = storage._path_for("bad-schema")
    path.write_text(json.dumps({"version": 1, "agents": "nope"}), encoding="utf-8")
    with pytest.raises(storage.StorageError, match="schema validation"):
        storage.load("bad-schema")


def test_rename_refuses_overwrite(temp_storage) -> None:
    storage.save("a", Design())
    storage.save("b", Design())
    with pytest.raises(storage.StorageError, match="already exists"):
        storage.rename("a", "b")


def test_rename_moves_file(temp_storage) -> None:
    storage.save("old", Design(agents=[Agent(name="a", role="r", goal="g", backstory="b")]))
    storage.rename("old", "new")
    assert storage.list_designs() == ["new"]
    assert storage.load("new").agents[0].name == "a"
