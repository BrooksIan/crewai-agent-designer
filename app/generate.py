"""Render a `Design` into CrewAI-canonical files and package them as a zip.

The four public functions mirror the four artifacts:
- ``to_agents_yaml(design)`` — ``config/agents.yaml``
- ``to_tasks_yaml(design)``  — ``config/tasks.yaml``
- ``to_crew_py(design)``     — ``crew.py`` (rendered from Jinja template)
- ``to_zip(design)``         — bytes of the full downloadable project

Prose fields (`role`, `goal`, `backstory`, `description`, `expected_output`)
are emitted as block-scalar folded strings (``>``) to match the style used
throughout the CrewAI docs and to keep multi-line prose readable in the
downloaded YAML.
"""

from __future__ import annotations

import io
from pathlib import Path
import zipfile

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .models import Agent, Design, Task, ToolConfig
from .tools_catalog import by_kind


# Fields that carry human-authored prose and should render as folded block
# scalars (`>`) in the YAML output rather than inline strings.
_AGENT_PROSE = {"role", "goal", "backstory"}
_TASK_PROSE = {"description", "expected_output"}


class _FoldedStr(str):
    """Marker subclass so PyYAML picks the folded (`>`) block scalar style."""


def _folded_representer(dumper: yaml.Dumper, data: _FoldedStr) -> yaml.ScalarNode:
    # Style ">" == folded block scalar, matching CrewAI docs' agents.yaml.
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style=">")


yaml.add_representer(_FoldedStr, _folded_representer, Dumper=yaml.SafeDumper)


def _agent_to_dict(a: Agent) -> dict:
    """Convert an Agent to the mapping shape agents.yaml expects.

    Only include optional fields when they differ from their default — keeps
    the YAML minimal and diff-friendly.
    """
    d: dict = {
        "role": _FoldedStr(a.role),
        "goal": _FoldedStr(a.goal),
        "backstory": _FoldedStr(a.backstory),
    }
    # Only serialize non-default optional fields to keep YAML minimal.
    if a.llm is not None:
        d["llm"] = a.llm
    if a.max_iter != 20:
        d["max_iter"] = a.max_iter
    if a.max_rpm is not None:
        d["max_rpm"] = a.max_rpm
    if a.verbose:
        d["verbose"] = True
    if a.allow_delegation:
        d["allow_delegation"] = True
    if a.reasoning:
        d["reasoning"] = True
    if a.multimodal:
        d["multimodal"] = True
    if not a.respect_context_window:
        d["respect_context_window"] = False
    if a.max_retry_limit != 2:
        d["max_retry_limit"] = a.max_retry_limit
    if a.inject_date:
        d["inject_date"] = True
    if a.date_format != "%Y-%m-%d":
        d["date_format"] = a.date_format
    return d


def _task_to_dict(t: Task) -> dict:
    """Convert a Task to the mapping shape tasks.yaml expects."""
    d: dict = {
        "description": _FoldedStr(t.description),
        "expected_output": _FoldedStr(t.expected_output),
    }
    if t.agent:
        d["agent"] = t.agent
    if t.async_execution:
        d["async_execution"] = True
    if t.human_input:
        d["human_input"] = True
    if t.markdown:
        d["markdown"] = True
    if t.output_file:
        d["output_file"] = t.output_file
    return d


def to_agents_yaml(design: Design) -> str:
    """Return the contents of ``config/agents.yaml``."""
    doc = {a.name: _agent_to_dict(a) for a in design.agents}
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def to_tasks_yaml(design: Design) -> str:
    """Return the contents of ``config/tasks.yaml``.

    Task order in the file follows ``design.crew.task_order`` when set,
    falling back to declaration order.
    """
    order = design.crew.task_order or [t.name for t in design.tasks]
    by_name = {t.name: t for t in design.tasks}
    doc = {name: _task_to_dict(by_name[name]) for name in order if name in by_name}
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def _tool_constructor(tool: ToolConfig) -> str:
    """Render the Python constructor call for a ToolConfig, e.g.
    ``SerperDevTool()`` or ``WebsiteSearchTool(website="https://example.com")``.
    """
    entry = by_kind(tool.kind)
    if entry is None:
        return f"{tool.kind}()"
    call_args: list[str] = []
    for spec in entry.params:
        value = tool.params.get(spec.name)
        if value in (None, ""):
            continue
        if spec.type == "str":
            call_args.append(f'{spec.name}="{value}"')
        else:
            call_args.append(f"{spec.name}={value!r}")
    return f"{entry.import_name}({', '.join(call_args)})"


def _tool_imports(design: Design) -> dict[str, list[str]]:
    """Group used tools by their import module → sorted list of class names."""
    used_kinds = {tool.kind for tool in design.tools if tool.name in _all_referenced_tools(design)}
    imports: dict[str, set[str]] = {}
    for kind in used_kinds:
        entry = by_kind(kind)
        if entry is None or not entry.import_module or not entry.import_name:
            continue
        imports.setdefault(entry.import_module, set()).add(entry.import_name)
    return {mod: sorted(names) for mod, names in imports.items()}


def _all_referenced_tools(design: Design) -> set[str]:
    """Names of every tool referenced from any agent or task."""
    used: set[str] = set()
    for a in design.agents:
        used.update(a.tools)
    for t in design.tasks:
        used.update(t.tools)
    return used


def _jinja_env() -> Environment:
    templates = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    def pytrue(v: bool) -> str:
        return "True" if v else "False"

    env.filters["pytrue"] = pytrue
    return env


def to_crew_py(design: Design) -> str:
    """Return the contents of ``crew.py`` for the exported project."""
    env = _jinja_env()
    template = env.get_template("crew.py.j2")
    tool_constructors = {tool.name: _tool_constructor(tool) for tool in design.tools}
    return template.render(
        crew=design.crew,
        agents=design.agents,
        tasks=design.tasks,
        imports=_tool_imports(design),
        tool_constructors=tool_constructors,
    )


def to_exported_readme(design: Design) -> str:
    """Return the ``README.md`` for the downloaded project."""
    env = _jinja_env()
    template = env.get_template("exported_readme.md.j2")
    return template.render(crew=design.crew, agents=design.agents, tasks=design.tasks)


def to_requirements_txt() -> str:
    """Pinned-ish requirements for a fresh CrewAI project.

    Kept intentionally minimal — the exported project runs a crew, it isn't a
    full application. Users add extras (e.g. serper) as they wire up tools.
    """
    return (
        "crewai>=0.51.0\n"
        "crewai-tools>=0.8.0\n"
    )


def to_zip(design: Design) -> bytes:
    """Assemble the full exported project as an in-memory zip.

    The zip's top-level directory name matches the crew's class name (lower-
    cased for filesystem friendliness), and the layout matches what CrewAI's
    ``@CrewBase`` decorator expects — ``config/agents.yaml`` and
    ``config/tasks.yaml`` alongside ``crew.py``.
    """
    root = design.crew.name.lower() or "crew"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/config/agents.yaml", to_agents_yaml(design))
        zf.writestr(f"{root}/config/tasks.yaml", to_tasks_yaml(design))
        zf.writestr(f"{root}/crew.py", to_crew_py(design))
        zf.writestr(f"{root}/requirements.txt", to_requirements_txt())
        zf.writestr(f"{root}/README.md", to_exported_readme(design))
    return buf.getvalue()
