"""Save, load, list, rename, and delete named crew designs on disk.

Designs are stored as JSON in ``./designs/`` relative to the app's working
directory — inside a Cloudera AI project that's the project root, so files
persist across app restarts and are visible in the file browser.

Workplaces organize designs into team/project namespaces. Metadata is stored
in ``./workplaces/`` as JSON index files; designs reference a workplace by name
via the ``Design.workplace`` field.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .models import Design, Workplace


DESIGNS_DIR = Path("designs")
WORKPLACES_DIR = Path("workplaces")
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


class StorageError(Exception):
    """Raised on invalid names, missing files, or serialization failures."""


def _ensure_dir() -> Path:
    DESIGNS_DIR.mkdir(parents=True, exist_ok=True)
    return DESIGNS_DIR


def _validate_name(name: str) -> None:
    # Kebab/snake, no path separators, no leading punctuation. Keeps filenames
    # safe on any FS and prevents users from writing outside DESIGNS_DIR.
    if not _VALID_NAME.match(name):
        raise StorageError(
            f"Invalid design name {name!r}: use letters, digits, dashes, "
            "or underscores (max 64 chars)."
        )


def _path_for(name: str) -> Path:
    _validate_name(name)
    return _ensure_dir() / f"{name}.json"


def list_designs() -> list[str]:
    """Return known design names sorted alphabetically."""
    if not DESIGNS_DIR.exists():
        return []
    return sorted(p.stem for p in DESIGNS_DIR.glob("*.json"))


def save(name: str, design: Design) -> None:
    """Write ``design`` to ``designs/{name}.json``."""
    path = _path_for(name)
    path.write_text(design.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")


def load(name: str) -> Design:
    """Read and parse the design at ``designs/{name}.json``."""
    path = _path_for(name)
    if not path.exists():
        raise StorageError(f"Design {name!r} not found.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StorageError(
            f"Design {name!r} at {path} is not valid JSON: {e}"
        ) from e
    try:
        return Design.model_validate(raw)
    except Exception as e:
        raise StorageError(f"Design {name!r} at {path} failed schema validation: {e}") from e


def delete(name: str) -> None:
    """Remove ``designs/{name}.json``. No error if it's already gone."""
    path = _path_for(name)
    path.unlink(missing_ok=True)


def rename(old: str, new: str) -> None:
    """Move ``designs/{old}.json`` to ``designs/{new}.json``.

    Refuses to overwrite an existing destination — callers who need
    replace-semantics should ``delete(new)`` first.
    """
    old_path = _path_for(old)
    new_path = _path_for(new)
    if not old_path.exists():
        raise StorageError(f"Design {old!r} not found.")
    if new_path.exists():
        raise StorageError(f"Design {new!r} already exists.")
    old_path.rename(new_path)


# ─────────────────────────────────────────────────────────────────────────────
# Workplace Management
# ─────────────────────────────────────────────────────────────────────────────


def _workplace_path(name: str) -> Path:
    """Return path for workplace metadata file."""
    _validate_name(name)
    WORKPLACES_DIR.mkdir(parents=True, exist_ok=True)
    return WORKPLACES_DIR / f"{name}.json"


def _now_iso() -> str:
    """Return current timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def create_workplace(name: str, description: str = "") -> Workplace:
    """Create a new workplace and save its metadata.

    Args:
        name: Unique workplace identifier (validated like design names).
        description: Human-readable purpose of the workspace.

    Returns:
        The created Workplace object.

    Raises:
        StorageError: If name is invalid or workspace already exists.
    """
    path = _workplace_path(name)
    if path.exists():
        raise StorageError(f"Workplace {name!r} already exists.")

    now = _now_iso()
    workplace = Workplace(
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
        members=[],
    )
    path.write_text(workplace.model_dump_json(indent=2), encoding="utf-8")
    return workplace


def load_workplace(name: str) -> Workplace:
    """Load a workplace by name.

    Args:
        name: The workplace identifier.

    Returns:
        The Workplace object.

    Raises:
        StorageError: If not found or not valid JSON.
    """
    path = _workplace_path(name)
    if not path.exists():
        raise StorageError(f"Workplace {name!r} not found.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StorageError(
            f"Workplace {name!r} at {path} is not valid JSON: {e}"
        ) from e
    try:
        return Workplace.model_validate(raw)
    except Exception as e:
        raise StorageError(
            f"Workplace {name!r} at {path} failed schema validation: {e}"
        ) from e


def list_workplaces() -> list[str]:
    """Return sorted list of all workplace names."""
    if not WORKPLACES_DIR.exists():
        return []
    return sorted(p.stem for p in WORKPLACES_DIR.glob("*.json"))


def update_workplace(name: str, description: str = "", members: list[str] | None = None) -> Workplace:
    """Update a workplace's metadata.

    Args:
        name: The workplace identifier.
        description: New description (if provided).
        members: New member list (if provided).

    Returns:
        The updated Workplace object.

    Raises:
        StorageError: If workplace not found.
    """
    workplace = load_workplace(name)
    if description:
        workplace.description = description
    if members is not None:
        workplace.members = members
    workplace.updated_at = _now_iso()

    path = _workplace_path(name)
    path.write_text(workplace.model_dump_json(indent=2), encoding="utf-8")
    return workplace


def delete_workplace(
    name: str,
    *,
    orphan_designs: Literal["unassign", "keep", "delete"] = "unassign",
) -> list[str]:
    """Delete a workplace and handle designs that referenced it.

    Args:
        name: Workplace identifier to remove.
        orphan_designs: What to do with designs whose ``workplace`` equals
            ``name``:
            - ``"unassign"`` (default) — clear ``Design.workplace`` so they
              remain editable under "Unassigned".
            - ``"keep"`` — leave the workplace field pointing at the deleted
              name (legacy behavior; designs become orphans).
            - ``"delete"`` — remove those design files along with the workplace.

    Returns:
        Names of designs that were unassigned or deleted.
    """
    affected = designs_in_workplace(name)
    if orphan_designs == "unassign":
        for design_name in affected:
            design = load(design_name)
            design.workplace = None
            save(design_name, design)
    elif orphan_designs == "delete":
        for design_name in affected:
            delete(design_name)
    # "keep" leaves Design.workplace untouched.

    path = _workplace_path(name)
    path.unlink(missing_ok=True)
    return affected


def designs_in_workplace(workplace_name: str) -> list[str]:
    """Return sorted list of designs in a given workplace.

    Args:
        workplace_name: The workplace to query.

    Returns:
        List of design names (without .json extension).
    """
    if not DESIGNS_DIR.exists():
        return []

    result = []
    for design_file in DESIGNS_DIR.glob("*.json"):
        try:
            design = load(design_file.stem)
            if design.workplace == workplace_name:
                result.append(design_file.stem)
        except StorageError:
            # Skip files that fail to parse
            continue

    return sorted(result)


def move_design_to_workplace(design_name: str, workplace_name: str) -> None:
    """Move a design to a different workplace.

    Args:
        design_name: The design to move.
        workplace_name: The target workplace name (or None to remove from any workplace).

    Raises:
        StorageError: If design or workplace not found.
    """
    design = load(design_name)
    if workplace_name is not None:
        # Validate the workplace exists
        load_workplace(workplace_name)
    design.workplace = workplace_name
    save(design_name, design)
