"""Cloudera Agent Studio (CAS) export.

Renders each declared `ToolConfig` in a design as a CAS tool template — a
directory containing `tool.py` + `requirements.txt` conforming to the shape
CAS accepts as an upload. Zip up the collection and the user drops it into
their CAS tool library; they compose the workflow inside CAS's own builder.

CAS's workflow definition file format is not exported by this module — see
`README.md` and `docs/exported-yaml.md` for why. This is deliberate: CAS only
ingests tools from an upload; workflows are authored in-app.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .models import Design, ToolConfig
from .tools_catalog import ToolCatalogEntry, by_kind, is_custom


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slug(name: str) -> str:
    """Convert a tool name to a safe, lowercase slug (kept short for readability)."""
    s = _SLUG_RE.sub("_", name).strip("_").lower()
    return s or "tool"


def _short_id(name: str) -> str:
    """Deterministic 8-char id derived from the tool name.

    Deterministic so re-exporting the same design produces byte-identical
    output — makes snapshot tests possible and gives users predictable diffs.
    Follows the shape observed in the CAS example dirs
    (``artifact_files_read_write_tool_15JDYu6m``).
    """
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return digest[:8]


def _slug_and_id(tool_name: str) -> str:
    """Return the ``<slug>_<8char>`` directory segment for one tool."""
    return f"{_slug(tool_name)}_{_short_id(tool_name)}"


# ---------------------------------------------------------------------------
# Jinja env
# ---------------------------------------------------------------------------

# Basic Pydantic type mapping — the catalog only uses these three today.
_PY_TYPE = {"str": "str", "int": "int", "bool": "bool"}


def _jinja_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates" / "cas"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    def py_type(t: str) -> str:
        return _PY_TYPE.get(t, "str")

    env.globals["py_type"] = py_type
    return env


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class CasExportError(Exception):
    """Raised when a tool's kind isn't in the catalog (bad design)."""


def to_cas_tool_py(tool: ToolConfig, entry: ToolCatalogEntry) -> str:
    """Render ``tool.py`` for one CAS-compatible tool."""
    template = _jinja_env().get_template("tool.py.j2")
    return template.render(tool=tool, entry=entry)


def to_cas_requirements(tool: ToolConfig, entry: ToolCatalogEntry) -> str:
    """Render ``requirements.txt`` for one CAS-compatible tool."""
    template = _jinja_env().get_template("requirements.txt.j2")
    return template.render(tool=tool, entry=entry)


def to_cas_tool_dir(tool: ToolConfig) -> dict[str, str]:
    """Return ``{filename: content}`` for one tool's dir contents.

    Used both by the zip export and by the Preview tab, so callers can render
    the generated files without going through disk.

    CustomTool instances write stored ``python_code`` / ``requirements``
    verbatim so a CAS import → edit → re-export round-trip preserves source.
    """
    if is_custom(tool.kind):
        if not (tool.python_code and tool.python_code.strip()):
            raise CasExportError(
                f"CustomTool {tool.name!r} has no tool.py source to export."
            )
        return {
            "tool.py": tool.python_code,
            "requirements.txt": tool.requirements or "",
        }
    entry = by_kind(tool.kind)
    if entry is None:
        raise CasExportError(f"Tool kind {tool.kind!r} is not in the catalog.")
    return {
        "tool.py": to_cas_tool_py(tool, entry),
        "requirements.txt": to_cas_requirements(tool, entry),
    }


def custom_tool_scaffold(tool_name: str = "custom_tool") -> str:
    """Return a blank CAS ``tool.py`` matching Agent Studio's custom-tool format.

    Mirrors the contract used by hand-authored CAS tools (``UserParameters``,
    ``ToolParameters``, ``run_tool``, ``OUTPUT_KEY``, ``__main__`` with
    ``--user-params`` / ``--tool-params``). Used to seed the Tools tab when
    the user adds a CustomTool.
    """
    name = (tool_name or "custom_tool").strip() or "custom_tool"
    template = _jinja_env().get_template("custom_tool.py.j2")
    return template.render(tool_name=name)


def custom_tool_requirements_scaffold() -> str:
    """Return the default CAS ``requirements.txt`` for a new custom tool."""
    template = _jinja_env().get_template("custom_requirements.txt.j2")
    return template.render()


def to_cas_tools_zip(design: Design) -> bytes:
    """Bundle every tool in ``design`` as a CAS upload.

    Layout mirrors the example under ``ClouderaAgentStudioeamples/`` — tool
    directories at the top level of the archive, ``<slug>_<hash>/tool.py``
    and ``<slug>_<hash>/requirements.txt``.

    Sorting by tool name and using ``ZIP_STORED`` with a fixed timestamp
    keeps the output byte-identical for identical inputs (the test suite and
    users' git diffs both benefit).
    """
    if not design.tools:
        raise CasExportError(
            "Design has no tools to export. Add at least one tool in the "
            "Tools tab before exporting to CAS."
        )

    buf = io.BytesIO()
    # ZIP_STORED avoids the compression codec adding non-deterministic bits.
    # For a handful of small text files this costs nothing.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        # Sort so output is deterministic regardless of design.tools order.
        for tool in sorted(design.tools, key=lambda t: t.name):
            dirname = _slug_and_id(tool.name)
            for filename, content in to_cas_tool_dir(tool).items():
                info = zipfile.ZipInfo(f"{dirname}/{filename}")
                # Fixed timestamp keeps zip bytes stable across runs.
                info.date_time = (2026, 1, 1, 0, 0, 0)
                info.external_attr = 0o644 << 16
                zf.writestr(info, content)
    return buf.getvalue()
