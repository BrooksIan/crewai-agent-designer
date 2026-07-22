"""Curated catalog of CrewAI tools the designer can wire into agents/tasks.

Each entry describes:
- ``import_path`` — the exact ``from … import …`` the exported ``crew.py`` will emit.
- ``params`` — the *constructor* params to render as form fields (become
  ``UserParameters`` fields on the CAS export). The UI reads this to build a
  per-tool param form; `app.generate` reads it to emit the right constructor
  call in ``crew.py``; `app.cas` reads it to build ``UserParameters``.
- ``runtime_params`` — args the agent supplies at call time (become
  ``ToolParameters`` fields on the CAS export). Not shown in the UI — these
  aren't things the workflow author configures.
- ``extra_requirements`` — deps beyond the base ``pydantic`` + ``crewai-tools``
  that the CAS tool's own venv needs.

Adding a new tool: append to `CATALOG` — no other module needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ParamType = Literal["str", "int", "bool"]


@dataclass(frozen=True)
class ToolParamSpec:
    name: str
    type: ParamType
    required: bool = False
    default: object | None = None
    help: str = ""


@dataclass(frozen=True)
class ToolCatalogEntry:
    kind: str                    # Key stored in ToolConfig.kind
    label: str                   # Shown in the dropdown
    import_module: str           # e.g. "crewai_tools"
    import_name: str             # e.g. "SerperDevTool"
    params: list[ToolParamSpec] = field(default_factory=list)
    runtime_params: list[ToolParamSpec] = field(default_factory=list)
    extra_requirements: list[str] = field(default_factory=list)
    description: str = ""


CATALOG: list[ToolCatalogEntry] = [
    ToolCatalogEntry(
        kind="SerperDevTool",
        label="Web Search (Serper.dev)",
        import_module="crewai_tools",
        import_name="SerperDevTool",
        params=[],
        runtime_params=[
            # CrewAI's SerperDevTool exposes `search_query`, not `query` —
            # matching the args_schema keeps the CAS wrapper compatible.
            ToolParamSpec(
                name="search_query",
                type="str",
                required=True,
                help="The search query.",
            ),
        ],
        extra_requirements=["requests"],
        description=(
            "Google-style web search via Serper.dev. Requires SERPER_API_KEY "
            "in the runtime environment; no constructor args."
        ),
    ),
    ToolCatalogEntry(
        kind="WebsiteSearchTool",
        label="Website Search (RAG)",
        import_module="crewai_tools",
        import_name="WebsiteSearchTool",
        params=[
            ToolParamSpec(
                name="website",
                type="str",
                required=True,
                help="URL of the site to index and search.",
            ),
        ],
        runtime_params=[
            # CrewAI's RAG search tools use `search_query` as the arg name.
            ToolParamSpec(
                name="search_query",
                type="str",
                required=True,
                help="Question or keywords to search for in the indexed site.",
            ),
        ],
        description="Semantic search restricted to one website.",
    ),
    ToolCatalogEntry(
        kind="ScrapeWebsiteTool",
        label="Scrape Website",
        import_module="crewai_tools",
        import_name="ScrapeWebsiteTool",
        params=[
            ToolParamSpec(
                name="website_url",
                type="str",
                required=False,
                help="Optional fixed URL. If omitted, the agent passes URLs at call time.",
            ),
        ],
        runtime_params=[
            ToolParamSpec(
                name="website_url",
                type="str",
                required=False,
                help="URL to scrape (only supply if the tool wasn't pinned to a fixed URL at config time).",
            ),
        ],
        description="Fetch and return the text of a web page.",
    ),
    ToolCatalogEntry(
        kind="FileReadTool",
        label="Read File",
        import_module="crewai_tools",
        import_name="FileReadTool",
        params=[
            ToolParamSpec(
                name="file_path",
                type="str",
                required=False,
                help="Optional fixed path. If omitted, the agent passes paths at call time.",
            ),
        ],
        runtime_params=[
            ToolParamSpec(
                name="file_path",
                type="str",
                required=False,
                help="Path of the file to read (only supply if not pinned at config time).",
            ),
        ],
        description="Read a text file from the local filesystem.",
    ),
    ToolCatalogEntry(
        kind="DirectoryReadTool",
        label="List Directory",
        import_module="crewai_tools",
        import_name="DirectoryReadTool",
        params=[
            ToolParamSpec(
                name="directory",
                type="str",
                required=False,
                help="Optional fixed directory. If omitted, the agent passes paths at call time.",
            ),
        ],
        runtime_params=[
            ToolParamSpec(
                name="directory",
                type="str",
                required=False,
                help="Directory to list (only supply if not pinned at config time).",
            ),
        ],
        description="List files in a directory.",
    ),
    ToolCatalogEntry(
        kind="BraveSearchTool",
        label="Web Search (Brave)",
        import_module="crewai_tools",
        import_name="BraveSearchTool",
        params=[],
        runtime_params=[
            ToolParamSpec(
                name="search_query",
                type="str",
                required=True,
                help="The search query.",
            ),
        ],
        description=(
            "Web search via Brave Search API. Requires BRAVE_API_KEY in the "
            "runtime environment; no constructor args."
        ),
    ),
    ToolCatalogEntry(
        kind="CustomTool",
        label="Custom (CAS)",
        # Not a crewai_tools class — source lives on ToolConfig.python_code.
        # CrewAI project export rejects CustomTool; CAS export writes the
        # stored tool.py / requirements.txt verbatim.
        import_module="",
        import_name="",
        params=[],
        description=(
            "Custom Cloudera Agent Studio tool. Edit tool.py and "
            "requirements.txt; re-export as a CAS workflow to round-trip."
        ),
    ),
]


def by_kind(kind: str) -> ToolCatalogEntry | None:
    """Return the catalog entry for ``kind``, or None if unknown."""
    for entry in CATALOG:
        if entry.kind == kind:
            return entry
    return None


def kinds() -> list[str]:
    """Return the list of known tool kinds in catalog order."""
    return [entry.kind for entry in CATALOG]


# Sentinel kind used by `app.import_yaml` when a YAML file references a
# tool by name but doesn't specify its type. Not a real catalog entry —
# `by_kind()` still returns None for it.
#
# Workflow:
# 1. Import stamps ``ToolConfig.kind = UNKNOWN_TOOL_KIND``.
# 2. The Tools tab shows a type picker for each unresolved tool.
# 3. ``app.validate.is_unresolved`` / :func:`is_unresolved` blocks export
#    until every placeholder is mapped to a real ``CATALOG`` kind.
UNKNOWN_TOOL_KIND = "_unknown_"

# Catalog kind for CAS-imported (or hand-authored) custom tools that carry
# their own ``tool.py`` / ``requirements.txt`` on ``ToolConfig``.
CUSTOM_TOOL_KIND = "CustomTool"


def is_unresolved(kind: str) -> bool:
    """True if ``kind`` is the placeholder stamped by YAML import.

    The Tools tab surfaces a picker for these; :mod:`app.validate` blocks
    export until every unresolved kind has been mapped to a real catalog
    entry.
    """
    return kind == UNKNOWN_TOOL_KIND


def is_custom(kind: str) -> bool:
    """True if ``kind`` is a CustomTool that stores CAS source on the model."""
    return kind == CUSTOM_TOOL_KIND
