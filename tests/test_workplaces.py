"""Tests for workplace storage and management."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app import storage
from app.models import Design, Workplace


@pytest.fixture
def temp_storage():
    """Temporarily redirect storage paths to a temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        with patch.object(storage, "DESIGNS_DIR", tmpdir / "designs"):
            with patch.object(storage, "WORKPLACES_DIR", tmpdir / "workplaces"):
                yield tmpdir


def test_create_workplace(temp_storage):
    """Test creating a new workplace."""
    wp = storage.create_workplace("research-team", description="ML research")

    assert wp.name == "research-team"
    assert wp.description == "ML research"
    assert wp.created_at is not None
    assert wp.updated_at is not None
    assert wp.members == []


def test_create_workplace_invalid_name(temp_storage):
    """Test that invalid names are rejected."""
    with pytest.raises(storage.StorageError):
        storage.create_workplace("invalid name!")


def test_create_workplace_duplicate(temp_storage):
    """Test that duplicate names are rejected."""
    storage.create_workplace("research-team")
    with pytest.raises(storage.StorageError):
        storage.create_workplace("research-team")


def test_load_workplace(temp_storage):
    """Test loading a workplace."""
    created = storage.create_workplace("research-team", description="ML research")
    loaded = storage.load_workplace("research-team")

    assert loaded.name == created.name
    assert loaded.description == created.description


def test_load_workplace_not_found(temp_storage):
    """Test that loading a missing workplace raises an error."""
    with pytest.raises(storage.StorageError):
        storage.load_workplace("nonexistent")


def test_list_workplaces(temp_storage):
    """Test listing workplaces."""
    storage.create_workplace("research-team")
    storage.create_workplace("product-team")
    storage.create_workplace("platform-team")

    workplaces = storage.list_workplaces()
    assert workplaces == ["platform-team", "product-team", "research-team"]  # sorted


def test_list_workplaces_empty(temp_storage):
    """Test listing when no workplaces exist."""
    workplaces = storage.list_workplaces()
    assert workplaces == []


def test_update_workplace(temp_storage):
    """Test updating a workplace."""
    storage.create_workplace("research-team")

    updated = storage.update_workplace(
        "research-team",
        description="Updated description",
        members=["alice@example.com", "bob@example.com"],
    )

    assert updated.description == "Updated description"
    assert updated.members == ["alice@example.com", "bob@example.com"]

    # Verify it persists
    reloaded = storage.load_workplace("research-team")
    assert reloaded.description == "Updated description"
    assert reloaded.members == ["alice@example.com", "bob@example.com"]


def test_delete_workplace(temp_storage):
    """Test deleting a workplace."""
    storage.create_workplace("research-team")
    storage.delete_workplace("research-team")

    with pytest.raises(storage.StorageError):
        storage.load_workplace("research-team")


def test_delete_workplace_missing(temp_storage):
    """Test that deleting a missing workplace doesn't raise an error."""
    # Should not raise
    storage.delete_workplace("nonexistent")


def test_move_design_to_workplace(temp_storage):
    """Test assigning a design to a workplace."""
    design = Design()
    storage.save("my-crew", design)
    storage.create_workplace("research-team")

    storage.move_design_to_workplace("my-crew", "research-team")

    loaded = storage.load("my-crew")
    assert loaded.workplace == "research-team"


def test_move_design_to_workplace_invalid_design(temp_storage):
    """Test that moving a missing design raises an error."""
    storage.create_workplace("research-team")

    with pytest.raises(storage.StorageError):
        storage.move_design_to_workplace("missing", "research-team")


def test_move_design_to_workplace_invalid_workplace(temp_storage):
    """Test that moving to a missing workplace raises an error."""
    design = Design()
    storage.save("my-crew", design)

    with pytest.raises(storage.StorageError):
        storage.move_design_to_workplace("my-crew", "missing-workspace")


def test_move_design_unassign(temp_storage):
    """Test unassigning a design from all workplaces."""
    design = Design(workplace="research-team")
    storage.save("my-crew", design)

    storage.move_design_to_workplace("my-crew", None)

    loaded = storage.load("my-crew")
    assert loaded.workplace is None


def test_designs_in_workplace(temp_storage):
    """Test querying designs in a workspace."""
    storage.create_workplace("research-team")
    storage.create_workplace("product-team")

    # Create designs
    d1 = Design(workplace="research-team")
    d2 = Design(workplace="research-team")
    d3 = Design(workplace="product-team")
    d4 = Design()  # unassigned

    storage.save("d1", d1)
    storage.save("d2", d2)
    storage.save("d3", d3)
    storage.save("d4", d4)

    # Query
    research_designs = storage.designs_in_workplace("research-team")
    assert set(research_designs) == {"d1", "d2"}

    product_designs = storage.designs_in_workplace("product-team")
    assert product_designs == ["d3"]


def test_designs_in_workplace_empty(temp_storage):
    """Test querying a workspace with no designs."""
    storage.create_workplace("empty-team")

    designs = storage.designs_in_workplace("empty-team")
    assert designs == []


def test_designs_in_workplace_nonexistent(temp_storage):
    """Test querying a nonexistent workspace (returns empty, doesn't raise)."""
    designs = storage.designs_in_workplace("nonexistent")
    assert designs == []


def test_workplace_json_structure(temp_storage):
    """Test that workplaces are stored in valid JSON format."""
    storage.create_workplace("research-team", description="ML research")
    storage.update_workplace("research-team", members=["alice@example.com"])

    wp_file = storage._workplace_path("research-team")
    raw = json.loads(wp_file.read_text(encoding="utf-8"))

    # Should parse as a Workplace
    wp = Workplace.model_validate(raw)
    assert wp.name == "research-team"
    assert wp.description == "ML research"
    assert wp.members == ["alice@example.com"]


def test_design_with_workplace_json(temp_storage):
    """Test that designs with workplace field are serialized correctly."""
    design = Design(workplace="research-team")
    storage.save("my-crew", design)

    design_file = storage._path_for("my-crew")
    raw = json.loads(design_file.read_text(encoding="utf-8"))

    # Should have workplace field
    assert raw["workplace"] == "research-team"

    # Should still deserialize
    loaded = Design.model_validate(raw)
    assert loaded.workplace == "research-team"
