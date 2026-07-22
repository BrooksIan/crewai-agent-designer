"""Tests for `app.llm` — config validation, JSON parsing, and HTTP retries."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app import llm


def test_validate_config_rejects_blank_fields() -> None:
    with pytest.raises(llm.LLMConfigError, match="API key"):
        llm.validate_config(base_url="https://api.openai.com/v1", api_key="", model="m")
    with pytest.raises(llm.LLMConfigError, match="Base URL"):
        llm.validate_config(base_url="", api_key="k", model="m")
    with pytest.raises(llm.LLMConfigError, match="Model"):
        llm.validate_config(base_url="https://api.openai.com/v1", api_key="k", model="")


def test_validate_config_rejects_relative_url() -> None:
    with pytest.raises(llm.LLMConfigError, match="absolute http"):
        llm.validate_config(base_url="not-a-url", api_key="k", model="m")


def test_build_from_config_rejects_bad_url() -> None:
    with pytest.raises(llm.LLMConfigError):
        llm.build_from_config(
            "openai",
            base_url="ftp://example.com",
            api_key="sk-test",
            model="gpt-4o-mini",
        )


def test_parse_json_strips_fence_and_prose() -> None:
    raw = 'Sure!\n```json\n{"role": "R", "goal": "G", "backstory": "B"}\n```'
    obj = llm._parse_json(raw, required={"role", "goal", "backstory"})
    assert obj == {"role": "R", "goal": "G", "backstory": "B"}


def test_parse_json_raises_on_empty() -> None:
    with pytest.raises(llm.LLMParseError, match="empty"):
        llm._parse_json("   ", required={"role"})


def test_parse_json_raises_on_missing_keys() -> None:
    with pytest.raises(llm.LLMParseError, match="missing keys"):
        llm._parse_json('{"role": "R"}', required={"role", "goal"})


def test_parse_json_raises_on_non_object() -> None:
    with pytest.raises(llm.LLMParseError, match="object"):
        llm._parse_json("[1, 2]", required={"role"})


def test_request_json_retries_on_429_then_succeeds() -> None:
    fail = MagicMock(
        status_code=429,
        text="rate limited",
        json=MagicMock(return_value={"error": {"message": "slow down"}}),
    )
    ok = MagicMock(status_code=200, json=MagicMock(return_value={"ok": True}))
    client = MagicMock()
    client.post.side_effect = [fail, ok]
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("app.llm.httpx.Client", return_value=client):
        with patch("app.llm.time.sleep"):
            data = llm._request_json(
                "https://example.com/v1/chat/completions",
                payload={},
                headers={},
                timeout=1.0,
                max_retries=2,
            )
    assert data == {"ok": True}
    assert client.post.call_count == 2


def test_request_json_does_not_retry_permanent_4xx() -> None:
    bad = MagicMock(
        status_code=401,
        text="unauthorized",
        json=MagicMock(return_value={"error": {"message": "bad key"}}),
    )
    client = MagicMock()
    client.post.return_value = bad
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("app.llm.httpx.Client", return_value=client):
        with pytest.raises(llm.LLMHTTPError, match="bad key") as exc_info:
            llm._request_json(
                "https://example.com/v1/chat/completions",
                payload={},
                headers={},
                timeout=1.0,
                max_retries=3,
            )
    assert exc_info.value.status_code == 401
    assert client.post.call_count == 1


def test_request_json_retries_timeout_then_raises() -> None:
    client = MagicMock()
    client.post.side_effect = httpx.TimeoutException("timed out")
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("app.llm.httpx.Client", return_value=client):
        with patch("app.llm.time.sleep"):
            with pytest.raises(llm.LLMHTTPError, match="timed out"):
                llm._request_json(
                    "https://example.com/v1/chat/completions",
                    payload={},
                    headers={},
                    timeout=1.0,
                    max_retries=1,
                )
    assert client.post.call_count == 2


def test_draft_agent_uses_parsed_json() -> None:
    client = llm.OpenAICompatibleClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        label="t",
        max_retries=0,
    )
    with patch.object(
        client,
        "_post",
        return_value='{"role": "Analyst", "goal": "Find facts", "backstory": "Careful."}',
    ):
        draft = client.draft_agent("research bot", "en")
    assert draft.role == "Analyst"
    assert draft.goal == "Find facts"
    assert draft.backstory == "Careful."


_SAMPLE_DESIGN_JSON = """
{
  "crew": {"name": "MarketCrew", "process": "sequential", "manager_llm": null},
  "agents": [
    {
      "name": "researcher",
      "role": "Researcher",
      "goal": "Find sources",
      "backstory": "Curious.",
      "tools": ["web_search"],
      "allow_delegation": false
    }
  ],
  "tasks": [
    {
      "name": "research_task",
      "description": "Gather sources",
      "expected_output": "Notes",
      "agent": "researcher",
      "context": []
    }
  ],
  "tools": [
    {"name": "web_search", "kind": "SerperDevTool", "params": {}}
  ]
}
"""


def test_draft_design_parses_fenced_json() -> None:
    client = llm.OpenAICompatibleClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        label="t",
    )
    fenced = f"```json\n{_SAMPLE_DESIGN_JSON}\n```"
    with patch.object(client, "_post", return_value=fenced) as post:
        draft = client.draft_design("research a market", "en")
    assert draft.crew.name == "MarketCrew"
    assert len(draft.agents) == 1
    assert draft.agents[0].name == "researcher"
    assert draft.tools[0].kind == "SerperDevTool"
    # Ensure we asked for a large token budget for design drafts.
    assert post.call_args.kwargs.get("max_tokens") == llm._DESIGN_MAX_TOKENS


def test_draft_design_missing_agents_raises() -> None:
    client = llm.OpenAICompatibleClient(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        label="t",
    )
    with patch.object(
        client,
        "_post",
        return_value='{"crew": {"name": "X"}, "agents": [], "tasks": []}',
    ):
        with pytest.raises(llm.LLMParseError, match="agents"):
            client.draft_design("anything", "en")


def test_design_system_prompt_lists_catalog_kinds() -> None:
    prompt = llm.design_system_prompt("en")
    assert "SerperDevTool" in prompt
    assert "CustomTool" not in prompt
    assert "crew" in prompt and "agents" in prompt and "tasks" in prompt
