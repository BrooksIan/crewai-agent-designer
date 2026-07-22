"""Cascade behavior for workplace deletion."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app import storage
from app.models import Design


@pytest.fixture
def temp_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        with patch.object(storage, "DESIGNS_DIR", root / "designs"):
            with patch.object(storage, "WORKPLACES_DIR", root / "workplaces"):
                yield root


def test_delete_workplace_unassigns_designs_by_default(temp_storage) -> None:
    storage.create_workplace("research-team")
    storage.save("crew-a", Design(workplace="research-team"))
    storage.save("crew-b", Design(workplace="research-team"))

    affected = storage.delete_workplace("research-team")
    assert set(affected) == {"crew-a", "crew-b"}
    assert storage.load("crew-a").workplace is None
    assert storage.load("crew-b").workplace is None
    assert storage.list_designs() == ["crew-a", "crew-b"]


def test_delete_workplace_can_delete_member_designs(temp_storage) -> None:
    storage.create_workplace("research-team")
    storage.save("crew-a", Design(workplace="research-team"))
    storage.save("keep-me", Design())

    affected = storage.delete_workplace("research-team", orphan_designs="delete")
    assert affected == ["crew-a"]
    assert storage.list_designs() == ["keep-me"]


def test_delete_workplace_keep_leaves_orphan_refs(temp_storage) -> None:
    storage.create_workplace("research-team")
    storage.save("crew-a", Design(workplace="research-team"))

    storage.delete_workplace("research-team", orphan_designs="keep")
    assert storage.load("crew-a").workplace == "research-team"
