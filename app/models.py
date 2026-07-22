"""Pydantic models describing a CrewAI crew design.

The `Design` model is the single source of truth for what the UI edits, what
`app.storage` serializes to JSON on disk, and what `app.generate` renders into
`agents.yaml`, `tasks.yaml`, and `crew.py`.

Field names mirror CrewAI's own agent/task/crew schemas so that exports are
mechanical: dump the model, drop the empty optionals, write YAML.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolConfig(BaseModel):
    """A CrewAI tool instance the user has declared in the Tools tab.

    `name` is a stable, user-chosen identifier referenced from agents and
    tasks. `kind` is the catalog key (e.g. ``SerperDevTool``) that maps to a
    concrete class in `crewai_tools` at export time. `params` holds
    tool-specific constructor arguments — the shape is validated by the tool
    catalog, not here.

    For ``kind == "CustomTool"`` (CAS-imported or hand-authored custom tools),
    ``python_code`` and ``requirements`` hold the CAS ``tool.py`` /
    ``requirements.txt`` bodies so Agent Studio re-export can round-trip.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Stable identifier used in agents/tasks")
    kind: str = Field(..., description="Catalog key from tools_catalog.CATALOG")
    params: dict[str, Any] = Field(default_factory=dict)
    python_code: str | None = Field(
        None, description="CAS tool.py source when kind is CustomTool"
    )
    requirements: str | None = Field(
        None, description="CAS requirements.txt when kind is CustomTool"
    )


class Agent(BaseModel):
    """CrewAI agent definition.

    Required fields (`role`, `goal`, `backstory`) match the CrewAI docs. The
    optional fields cover the subset the designer surfaces in the UI — enough
    to configure production crews without exposing internal or deprecated
    fields (`allow_code_execution`, `code_execution_mode`).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="YAML key under agents.yaml")
    # Required by CrewAI
    role: str
    goal: str
    backstory: str
    # Optional passthroughs
    llm: str | None = None
    tools: list[str] = Field(default_factory=list, description="ToolConfig.name refs")
    max_iter: int = 20
    max_rpm: int | None = None
    verbose: bool = False
    allow_delegation: bool = False
    reasoning: bool = False
    multimodal: bool = False
    respect_context_window: bool = True
    max_retry_limit: int = 2
    inject_date: bool = False
    date_format: str = "%Y-%m-%d"


class Task(BaseModel):
    """CrewAI task definition.

    Tasks reference an agent by name (matching the ``@agent`` method name in
    the generated `crew.py`) and may declare a `context` list of task names
    whose outputs feed this task. Validation catches missing references and
    cycles before export.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="YAML key under tasks.yaml")
    description: str
    expected_output: str
    agent: str | None = Field(None, description="Agent.name ref")
    tools: list[str] = Field(default_factory=list, description="ToolConfig.name refs")
    context: list[str] = Field(
        default_factory=list, description="Task.name refs — outputs used as input"
    )
    async_execution: bool = False
    human_input: bool = False
    markdown: bool = False
    output_file: str | None = None


class Crew(BaseModel):
    """Crew-level orchestration config.

    The `name` becomes the Python class name in the exported `crew.py`, so it
    must be a valid identifier. `task_order` optionally reorders task
    execution; if empty the tasks run in declaration order.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field("MyCrew", description="Python class name for crew.py")
    process: Literal["sequential", "hierarchical"] = "sequential"
    verbose: bool = True
    memory: bool = False
    cache: bool = True
    manager_llm: str | None = Field(
        None, description="Required when process == 'hierarchical'"
    )
    task_order: list[str] = Field(
        default_factory=list,
        description="Optional explicit task ordering; empty = declaration order",
    )


class Design(BaseModel):
    """Top-level design object — one file on disk, one entry in st.session_state."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    agents: list[Agent] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    tools: list[ToolConfig] = Field(default_factory=list)
    crew: Crew = Field(default_factory=Crew)
    workplace: str | None = Field(
        None, description="Optional workplace (team/organization) this design belongs to"
    )

    def agent_names(self) -> list[str]:
        return [a.name for a in self.agents]

    def task_names(self) -> list[str]:
        return [t.name for t in self.tasks]

    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]


class Workplace(BaseModel):
    """Organizational container for related crew designs.

    Workplaces group designs (crews) by team, project, or use-case. Each workplace
    has a unique name and optional description. Designs reference a workplace by name.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique workplace identifier")
    description: str = Field("", description="Human-readable workspace purpose")
    created_at: str = Field(..., description="ISO 8601 timestamp of creation")
    updated_at: str = Field(..., description="ISO 8601 timestamp of last update")
    members: list[str] = Field(
        default_factory=list, description="Optional list of member identifiers"
    )

    def design_count(self) -> int:
        """Return the number of designs in this workplace (computed from storage)."""
        # This is overridden at runtime by storage functions
        return 0
