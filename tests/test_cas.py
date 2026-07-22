"""Tests for `app.cas` — CAS-compatible tool template export.

Covers:
- Every catalog kind renders a `tool.py` that parses as valid Python and
  defines the four top-level names CAS requires.
- The generated `UserParameters` and `ToolParameters` classes are usable
  Pydantic models (can be instantiated).
- The `__main__` block is present and wired to argparse.
- The `requirements.txt` includes the base pins and per-tool extras.
- The zip layout matches CAS's expectations (top-level `<slug>_<hash>/…`).
- Repeated exports of the same design produce byte-identical zips.
- Empty designs (no tools) raise a clear error.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import sys
import types
import zipfile
from pathlib import Path

import pytest

from app import cas
from app.models import Design, ToolConfig
from app.tools_catalog import CATALOG, by_kind, is_custom


REQUIRED_TOP_LEVEL_NAMES = {
    "UserParameters",
    "ToolParameters",
    "run_tool",
    "OUTPUT_KEY",
}

# CustomTool is not Jinja-rendered — it stores source on ToolConfig. Exclude
# it from the generated-template parametrize suite.
GENERATABLE_CATALOG = [e for e in CATALOG if not is_custom(e.kind)]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _top_level_names(source: str) -> set[str]:
    """Return every symbol defined at module scope in ``source``."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _has_main_block(source: str) -> bool:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Look for the classic `if __name__ == "__main__":` shape.
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
        ):
            return True
    return False


def _load_generated(source: str, module_name: str) -> types.ModuleType:
    """Import ``source`` as a module without touching disk.

    Injects a stub `crewai_tools` module so the generated ``from crewai_tools
    import …`` doesn't require the package (kept out of the test dep set).
    """
    stub = types.ModuleType("crewai_tools")

    class _Stub:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run(self, **kwargs):  # noqa: D401 — stub, not documenting
            return {"echo": kwargs, "config": self.kwargs}

    for kind in [e.import_name for e in CATALOG]:
        setattr(stub, kind, _Stub)
    sys.modules["crewai_tools"] = stub

    spec = importlib.util.spec_from_loader(module_name, loader=None)
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so Pydantic's forward-reference
    # resolution can find `Optional` and friends via the module's globals.
    # Without this, class-build-time evaluation of ``Optional[str]`` on
    # Pydantic 2 raises `class-not-fully-defined`.
    sys.modules[module_name] = module
    exec(compile(source, f"<{module_name}>", "exec"), module.__dict__)
    return module


# ---------------------------------------------------------------------------
# per-kind parse + shape checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry", GENERATABLE_CATALOG, ids=[e.kind for e in GENERATABLE_CATALOG])
def test_generated_tool_py_is_valid_python(entry) -> None:
    tool = ToolConfig(name=f"my_{entry.kind.lower()}", kind=entry.kind, params={})
    source = cas.to_cas_tool_py(tool, entry)
    ast.parse(source)  # raises SyntaxError on failure


@pytest.mark.parametrize("entry", GENERATABLE_CATALOG, ids=[e.kind for e in GENERATABLE_CATALOG])
def test_generated_tool_py_defines_required_symbols(entry) -> None:
    tool = ToolConfig(name=f"my_{entry.kind.lower()}", kind=entry.kind, params={})
    source = cas.to_cas_tool_py(tool, entry)
    assert REQUIRED_TOP_LEVEL_NAMES <= _top_level_names(source)


@pytest.mark.parametrize("entry", GENERATABLE_CATALOG, ids=[e.kind for e in GENERATABLE_CATALOG])
def test_generated_tool_py_has_main_block(entry) -> None:
    tool = ToolConfig(name=f"my_{entry.kind.lower()}", kind=entry.kind, params={})
    source = cas.to_cas_tool_py(tool, entry)
    assert _has_main_block(source)


@pytest.mark.parametrize("entry", GENERATABLE_CATALOG, ids=[e.kind for e in GENERATABLE_CATALOG])
def test_generated_tool_py_carries_studio_marker_alias(entry) -> None:
    """CAS's ingestion may look for this alias; matches the reference tools."""
    tool = ToolConfig(name=f"my_{entry.kind.lower()}", kind=entry.kind, params={})
    source = cas.to_cas_tool_py(tool, entry)
    assert "from pydantic import BaseModel as StudioBaseTool" in source


@pytest.mark.parametrize("entry", GENERATABLE_CATALOG, ids=[e.kind for e in GENERATABLE_CATALOG])
def test_generated_module_imports_and_pydantic_models_instantiate(entry) -> None:
    tool = ToolConfig(name=f"my_{entry.kind.lower()}", kind=entry.kind, params={})
    source = cas.to_cas_tool_py(tool, entry)
    module = _load_generated(source, f"cas_{entry.kind}")
    # UserParameters accepts an empty dict — every field is optional there.
    module.UserParameters()
    # ToolParameters may have required fields; supply plausible defaults.
    required_args = {}
    for p in entry.runtime_params:
        if p.required:
            required_args[p.name] = "test" if p.type == "str" else 0
    module.ToolParameters(**required_args)
    # OUTPUT_KEY is the exact string CAS greps for.
    assert module.OUTPUT_KEY == "tool_output"


def test_field_descriptions_survive_to_generated_source() -> None:
    """The 'description=...' text from the catalog must appear in the file —
    it's what CAS surfaces to the LLM at tool-selection time."""
    entry = by_kind("SerperDevTool")
    tool = ToolConfig(name="web_search", kind="SerperDevTool", params={})
    source = cas.to_cas_tool_py(tool, entry)
    # The catalog says "The search query." for SerperDev's runtime `query`.
    assert "The search query." in source


# ---------------------------------------------------------------------------
# requirements.txt
# ---------------------------------------------------------------------------

def test_requirements_contains_base_pins() -> None:
    entry = by_kind("SerperDevTool")
    tool = ToolConfig(name="web_search", kind="SerperDevTool", params={})
    req = cas.to_cas_requirements(tool, entry)
    assert "pydantic>=2.10" in req
    assert "crewai-tools>=0.8.0" in req


def test_requirements_includes_extra_requirements() -> None:
    """SerperDevTool declares ``requests`` as an extra dep — it should
    appear in the generated requirements.txt for that tool."""
    entry = by_kind("SerperDevTool")
    tool = ToolConfig(name="web_search", kind="SerperDevTool", params={})
    req = cas.to_cas_requirements(tool, entry)
    assert "requests" in req


def test_requirements_omits_extras_when_none() -> None:
    entry = by_kind("WebsiteSearchTool")
    tool = ToolConfig(name="site_search", kind="WebsiteSearchTool", params={"website": "https://x.com"})
    req = cas.to_cas_requirements(tool, entry)
    # WebsiteSearchTool has no extra_requirements — no stray entries beyond
    # the base pins.
    assert "requests" not in req


# ---------------------------------------------------------------------------
# zip layout + determinism
# ---------------------------------------------------------------------------

def _sample_design_with_tools() -> Design:
    return Design(
        tools=[
            ToolConfig(name="web_search", kind="SerperDevTool", params={}),
            ToolConfig(
                name="crewai_docs_search",
                kind="WebsiteSearchTool",
                params={"website": "https://docs.crewai.com"},
            ),
        ]
    )


def test_zip_layout_matches_cas_expectation() -> None:
    data = cas.to_cas_tools_zip(_sample_design_with_tools())
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
    # Each tool has its own top-level dir with the two required files.
    dirs = {name.split("/")[0] for name in names}
    assert any(d.startswith("web_search_") for d in dirs)
    assert any(d.startswith("crewai_docs_search_") for d in dirs)
    for d in dirs:
        assert f"{d}/tool.py" in names
        assert f"{d}/requirements.txt" in names


def test_zip_is_deterministic() -> None:
    """Byte-identical output for identical input — matters for snapshot tests
    and gives users predictable diffs when they check exports into git."""
    design = _sample_design_with_tools()
    assert cas.to_cas_tools_zip(design) == cas.to_cas_tools_zip(design)


def test_zip_dir_id_is_deterministic() -> None:
    """The 8-char hash suffix depends only on the tool name."""
    assert cas._slug_and_id("web_search") == cas._slug_and_id("web_search")
    assert cas._slug_and_id("web_search") != cas._slug_and_id("other_name")


def test_empty_design_raises_with_actionable_message() -> None:
    with pytest.raises(cas.CasExportError) as excinfo:
        cas.to_cas_tools_zip(Design())
    assert "Tools tab" in str(excinfo.value)


def test_unknown_tool_kind_raises() -> None:
    bad = Design(tools=[ToolConfig(name="bogus", kind="NotARealTool", params={})])
    with pytest.raises(cas.CasExportError):
        cas.to_cas_tools_zip(bad)


def test_zip_contents_compile(tmp_path: Path) -> None:
    """Extract the zip and verify each tool.py compiles as Python."""
    data = cas.to_cas_tools_zip(_sample_design_with_tools())
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(tmp_path)
    for tool_py in tmp_path.rglob("tool.py"):
        source = tool_py.read_text()
        ast.parse(source)
