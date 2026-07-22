"""Tests for `app.import_yaml`.

Two axes of coverage:

- **Round-trip against our own generator.** Export a fixture design to YAML,
  parse it back with :func:`app.import_yaml.from_yaml`, and assert the
  reconstituted `Design` equals the original — modulo fields the CrewAI
  YAML schema doesn't preserve (task `context`, tool params, `Crew` config).
  If :mod:`app.generate` ever changes its output shape without a matching
  parser update, this test fails loudly.

- **Failure modes and edge cases.** Broken YAML, missing optionals, extra
  keys, unknown tools/agents, BOM, no trailing newline. Every error path is
  exercised so the UI never has to guess what the parser is doing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import generate, import_yaml
from app.import_yaml import UNKNOWN_TOOL_KIND, ImportError, InvalidYamlError, SchemaError
from app.models import Agent, Design, Task, ToolConfig


# ---------------------------------------------------------------------------
# Round-trip against our own generator
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_agent_and_task_names(simple_design: Design) -> None:
    """Names survive export → import unchanged."""
    agents_yaml = generate.to_agents_yaml(simple_design)
    tasks_yaml = generate.to_tasks_yaml(simple_design)
    result = import_yaml.from_yaml(agents_yaml, tasks_yaml)

    assert [a.name for a in result.design.agents] == [
        a.name for a in simple_design.agents
    ]
    assert [t.name for t in result.design.tasks] == [
        t.name for t in simple_design.tasks
    ]


def test_roundtrip_preserves_agent_prose(simple_design: Design) -> None:
    """Folded block scalars decode back to the same strings we exported.

    Prose fields (`role`, `goal`, `backstory`) render with `>-` in our
    output. PyYAML `safe_load` collapses the folded form to a single-line
    string with the leading whitespace stripped — that's what CrewAI
    itself does, so the value we get back matches the original the user
    typed.
    """
    agents_yaml = generate.to_agents_yaml(simple_design)
    tasks_yaml = generate.to_tasks_yaml(simple_design)
    result = import_yaml.from_yaml(agents_yaml, tasks_yaml)

    for original, reparsed in zip(simple_design.agents, result.design.agents):
        assert original.role == reparsed.role
        assert original.goal == reparsed.goal
        assert original.backstory == reparsed.backstory


def test_roundtrip_preserves_non_default_optional_fields(simple_design: Design) -> None:
    """The exporter only writes optional fields that differ from the model
    default; the importer must fill the rest from defaults so the reparsed
    Agents equal the originals."""
    agents_yaml = generate.to_agents_yaml(simple_design)
    tasks_yaml = generate.to_tasks_yaml(simple_design)
    result = import_yaml.from_yaml(agents_yaml, tasks_yaml)

    for original, reparsed in zip(simple_design.agents, result.design.agents):
        assert original.verbose == reparsed.verbose
        assert original.max_iter == reparsed.max_iter
        assert original.max_retry_limit == reparsed.max_retry_limit


def test_roundtrip_preserves_task_agent_reference(simple_design: Design) -> None:
    agents_yaml = generate.to_agents_yaml(simple_design)
    tasks_yaml = generate.to_tasks_yaml(simple_design)
    result = import_yaml.from_yaml(agents_yaml, tasks_yaml)

    for original, reparsed in zip(simple_design.tasks, result.design.tasks):
        assert original.agent == reparsed.agent


def test_roundtrip_does_not_preserve_tools_or_context(simple_design: Design) -> None:
    """Documented projection: our own exporter drops `agent.tools`,
    `task.tools`, and `task.context` — CrewAI YAML doesn't have a
    per-agent tool field, and we haven't opted into the per-task one.
    So a round-trip through our own YAML yields empty lists here."""
    agents_yaml = generate.to_agents_yaml(simple_design)
    tasks_yaml = generate.to_tasks_yaml(simple_design)
    result = import_yaml.from_yaml(agents_yaml, tasks_yaml)

    for a in result.design.agents:
        assert a.tools == []
    for t in result.design.tasks:
        assert t.context == []


# ---------------------------------------------------------------------------
# Hand-authored fixture (the example the app ships with)
# ---------------------------------------------------------------------------

def test_hand_authored_example_parses() -> None:
    """The example YAML we ship in `examples/simple-research-crew/config/`
    imports cleanly with no errors and no warnings."""
    project = Path(__file__).parent.parent
    agents_text = (project / "examples/simple-research-crew/config/agents.yaml").read_text()
    tasks_text = (project / "examples/simple-research-crew/config/tasks.yaml").read_text()

    result = import_yaml.from_yaml(agents_text, tasks_text)

    assert [a.name for a in result.design.agents] == ["researcher", "writer"]
    assert [t.name for t in result.design.tasks] == ["research", "write_brief"]
    assert result.design.tasks[1].markdown is True
    # No tools are declared in agents.yaml/tasks.yaml directly.
    assert result.design.tools == []
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Field handling edge cases
# ---------------------------------------------------------------------------

_MINIMAL_AGENT_YAML = """
minimal:
  role: One
  goal: Do it
  backstory: Because
"""

_HAND_AUTHORED_TOOLS_YAML = """
researcher:
  role: R
  goal: G
  backstory: B
  tools:
    - web_search
    - wiki
"""


def test_missing_optional_fields_use_model_defaults() -> None:
    """An agent with only required fields imports with defaults elsewhere."""
    result = import_yaml.from_yaml(_MINIMAL_AGENT_YAML, "")
    agent = result.design.agents[0]
    assert agent.name == "minimal"
    assert agent.role == "One"
    assert agent.verbose is False           # model default
    assert agent.max_iter == 20              # model default
    assert agent.respect_context_window is True


def test_extra_fields_are_dropped_with_warning() -> None:
    """Unknown keys on an agent don't fail the import — they warn."""
    yaml_text = """
researcher:
  role: R
  goal: G
  backstory: B
  future_field: 42
"""
    result = import_yaml.from_yaml(yaml_text, "")
    assert result.design.agents[0].name == "researcher"
    assert any("future_field" in w for w in result.warnings)


def test_agent_tools_produce_unresolved_tool_configs() -> None:
    """CrewAI YAML references tools by name only — the importer must
    materialize a placeholder ToolConfig per unique reference so the user
    can pick the real type on the Tools tab."""
    result = import_yaml.from_yaml(_HAND_AUTHORED_TOOLS_YAML, "")

    assert {t.name for t in result.design.tools} == {"web_search", "wiki"}
    assert all(t.kind == UNKNOWN_TOOL_KIND for t in result.design.tools)
    # Warning specifically points at the count of unresolved tools.
    assert any("2 tool" in w for w in result.warnings)


def test_task_tools_are_also_extracted() -> None:
    """`task.tools` references are unioned into the tool list."""
    tasks_yaml = """
research:
  description: D
  expected_output: O
  tools:
    - task_only_tool
"""
    result = import_yaml.from_yaml(_MINIMAL_AGENT_YAML, tasks_yaml)
    assert "task_only_tool" in [t.name for t in result.design.tools]


def test_unknown_agent_reference_on_task_warns_but_does_not_fail() -> None:
    """A task pointing at an agent that isn't defined imports as-is; the
    existing validator catches it later, which is more useful than
    failing the whole import."""
    tasks_yaml = """
orphan:
  description: D
  expected_output: O
  agent: nonexistent
"""
    result = import_yaml.from_yaml(_MINIMAL_AGENT_YAML, tasks_yaml)
    assert result.design.tasks[0].agent == "nonexistent"
    assert any("nonexistent" in w and "unknown agent" in w for w in result.warnings)


def test_string_instead_of_list_for_tools_becomes_single_element() -> None:
    """CrewAI tutorials sometimes write `tools: some_tool` (bare string).
    Accept it as a single-element list rather than rejecting."""
    yaml_text = """
r:
  role: R
  goal: G
  backstory: B
  tools: solo_tool
"""
    result = import_yaml.from_yaml(yaml_text, "")
    assert result.design.agents[0].tools == ["solo_tool"]


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_top_level_list_is_rejected_with_clear_message() -> None:
    """agents.yaml must be a mapping, not a list."""
    with pytest.raises(InvalidYamlError) as exc:
        import_yaml.from_yaml("- role: R", "")
    assert "top level must be a mapping" in str(exc.value)


def test_syntactically_broken_yaml_is_rejected() -> None:
    """PyYAML's parse error is wrapped in InvalidYamlError with the filename."""
    with pytest.raises(InvalidYamlError) as exc:
        import_yaml.from_yaml("this: is: not: valid: [yaml", "")
    assert "agents.yaml" in str(exc.value)


def test_agent_body_that_is_not_a_mapping_is_a_schema_error() -> None:
    """`role: string` at the wrong level fails cleanly."""
    with pytest.raises(SchemaError) as exc:
        import_yaml.from_yaml("bad: just_a_string", "")
    assert "bad" in str(exc.value)


def test_pydantic_validation_error_is_reraised_as_schema_error() -> None:
    """A wrong-type field (e.g. `max_iter: 'not a number'`) wraps into SchemaError."""
    yaml_text = """
bad:
  role: R
  goal: G
  backstory: B
  max_iter: not-a-number
"""
    with pytest.raises(SchemaError) as exc:
        import_yaml.from_yaml(yaml_text, "")
    assert "bad" in str(exc.value)


def test_empty_agents_yaml_is_valid() -> None:
    """An empty document produces zero agents. Useful for staged imports
    where the user paste-uploads only tasks first."""
    result = import_yaml.from_yaml("", "")
    assert result.design.agents == []
    assert result.design.tasks == []


def test_utf8_bom_is_tolerated() -> None:
    """Files saved on Windows sometimes carry a BOM. It must not break parse."""
    yaml_text = "﻿" + _MINIMAL_AGENT_YAML
    result = import_yaml.from_yaml(yaml_text, "")
    assert result.design.agents[0].name == "minimal"


def test_missing_trailing_newline_is_tolerated() -> None:
    """PyYAML handles this cleanly, but confirm we don't accidentally add
    logic that assumes one."""
    yaml_text = _MINIMAL_AGENT_YAML.strip()  # strip trailing \n
    result = import_yaml.from_yaml(yaml_text, "")
    assert result.design.agents[0].name == "minimal"


# ---------------------------------------------------------------------------
# Interop with the validator
# ---------------------------------------------------------------------------

def test_imported_design_with_unresolved_tools_is_blocked_by_validate() -> None:
    """Importing produces `_unknown_` tools; validate flags them so Export
    is blocked until the user resolves each one on the Tools tab."""
    from app import validate

    result = import_yaml.from_yaml(_HAND_AUTHORED_TOOLS_YAML, "")
    errors = validate.validate(result.design)
    unresolved_errs = [e for e in errors if "imported from YAML" in e.message]
    assert len(unresolved_errs) == 2  # web_search + wiki
