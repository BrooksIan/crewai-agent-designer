"""Tests for canvas manager enable / disable actions."""

from __future__ import annotations

from app import canvas_actions, graph
from app.models import Agent, Crew, Design


def test_enable_manager_switches_to_hierarchical() -> None:
    d = Design(agents=[Agent(name="a", role="r", goal="g", backstory="b")])
    warnings = canvas_actions.enable_manager(d, "gpt-4o")
    assert warnings == []
    assert d.crew.process == "hierarchical"
    assert d.crew.manager_llm == "gpt-4o"
    kinds = [n.kind for n in graph.design_to_graph(d)[0]]
    assert "manager" in kinds


def test_enable_manager_rejects_blank_llm() -> None:
    d = Design(crew=Crew(name="C"))
    warnings = canvas_actions.enable_manager(d, "  ")
    assert warnings
    assert d.crew.process == "sequential"
    assert d.crew.manager_llm is None


def test_update_manager_llm() -> None:
    d = Design(crew=Crew(name="C", process="hierarchical", manager_llm="old"))
    warnings = canvas_actions.update_manager_llm(d, "new-model")
    assert warnings == []
    assert d.crew.manager_llm == "new-model"


def test_disable_manager_returns_to_sequential() -> None:
    d = Design(crew=Crew(name="C", process="hierarchical", manager_llm="gpt-4o"))
    canvas_actions.disable_manager(d)
    assert d.crew.process == "sequential"
    assert d.crew.manager_llm is None
    kinds = [n.kind for n in graph.design_to_graph(d)[0]]
    assert "manager" not in kinds
