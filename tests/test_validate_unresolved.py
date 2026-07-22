"""Extra validation coverage for unresolved imported tools."""

from __future__ import annotations

from app import validate
from app.models import Agent, Crew, Design, ToolConfig


def test_unresolved_tool_kind_blocks_export() -> None:
    design = Design(
        agents=[Agent(name="a", role="r", goal="g", backstory="b", tools=["web"])],
        tools=[ToolConfig(name="web", kind="_unknown_")],
        crew=Crew(name="C"),
    )
    errors = validate.validate(design)
    messages = [e.message for e in errors]
    assert any("imported from YAML" in m for m in messages)
    assert any(e.where == "tools[0].kind" for e in errors)
