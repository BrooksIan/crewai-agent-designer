"""Tests for `app.design_from_prompt` — draft → Design assembly."""

from __future__ import annotations

import pytest

from app import design_from_prompt
from app.llm import (
    DesignAgentSpec,
    DesignCrewSpec,
    DesignDraft,
    DesignTaskSpec,
    DesignToolSpec,
)


def _sample_draft(**overrides) -> DesignDraft:
    base = dict(
        crew=DesignCrewSpec(name="ResearchCrew", process="sequential"),
        agents=(
            DesignAgentSpec(
                name="researcher",
                role="Researcher",
                goal="Find sources",
                backstory="Curious analyst.",
                tools=("web_search",),
            ),
            DesignAgentSpec(
                name="writer",
                role="Writer",
                goal="Draft brief",
                backstory="Clear communicator.",
                tools=(),
            ),
        ),
        tasks=(
            DesignTaskSpec(
                name="research_task",
                description="Gather sources",
                expected_output="Bullet list of findings",
                agent="researcher",
                context=(),
            ),
            DesignTaskSpec(
                name="write_task",
                description="Write the brief",
                expected_output="One-page markdown brief",
                agent="writer",
                context=("research_task",),
            ),
        ),
        tools=(
            DesignToolSpec(name="web_search", kind="SerperDevTool", params={}),
        ),
    )
    base.update(overrides)
    return DesignDraft(**base)


def test_from_draft_builds_design() -> None:
    result = design_from_prompt.from_draft(_sample_draft())
    d = result.design
    assert len(d.agents) == 2
    assert len(d.tasks) == 2
    assert len(d.tools) == 1
    assert d.tools[0].kind == "SerperDevTool"
    assert d.agents[0].tools == ["web_search"]
    assert d.tasks[1].context == ["research_task"]
    assert d.tasks[0].agent == "researcher"
    assert d.crew.name == "ResearchCrew"
    assert d.crew.process == "sequential"


def test_unknown_tool_kind_dropped_with_warning() -> None:
    draft = _sample_draft(
        tools=(
            DesignToolSpec(name="magic", kind="NotARealTool", params={}),
            DesignToolSpec(name="web_search", kind="SerperDevTool", params={}),
        ),
        agents=(
            DesignAgentSpec(
                name="researcher",
                role="R",
                goal="G",
                backstory="B",
                tools=("magic", "web_search"),
            ),
        ),
        tasks=(
            DesignTaskSpec(
                name="t1",
                description="d",
                expected_output="o",
                agent="researcher",
            ),
        ),
    )
    result = design_from_prompt.from_draft(draft)
    assert all(t.kind != "NotARealTool" for t in result.design.tools)
    assert any("NotARealTool" in w for w in result.warnings)
    assert "magic" not in result.design.agents[0].tools
    assert "web_search" in result.design.agents[0].tools


def test_hierarchical_defaults_manager_llm() -> None:
    draft = _sample_draft(
        crew=DesignCrewSpec(name="MgrCrew", process="hierarchical", manager_llm=None),
    )
    result = design_from_prompt.from_draft(draft)
    assert result.design.crew.process == "hierarchical"
    assert result.design.crew.manager_llm == "gpt-4o"
    assert any("manager_llm" in w for w in result.warnings)


def test_empty_agents_raises() -> None:
    draft = DesignDraft(
        crew=DesignCrewSpec(name="Empty"),
        agents=(),
        tasks=(
            DesignTaskSpec(
                name="t", description="d", expected_output="o", agent=None
            ),
        ),
    )
    with pytest.raises(design_from_prompt.AssembleError, match="no agents"):
        design_from_prompt.from_draft(draft)


def test_infers_tool_from_agent_kind_ref() -> None:
    draft = _sample_draft(
        tools=(),
        agents=(
            DesignAgentSpec(
                name="researcher",
                role="R",
                goal="G",
                backstory="B",
                tools=("SerperDevTool",),
            ),
        ),
        tasks=(
            DesignTaskSpec(
                name="t1",
                description="d",
                expected_output="o",
                agent="researcher",
            ),
        ),
    )
    result = design_from_prompt.from_draft(draft)
    assert len(result.design.tools) == 1
    assert result.design.tools[0].kind == "SerperDevTool"
    assert result.design.agents[0].tools
