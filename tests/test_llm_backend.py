"""Tests for LLM backend key normalization used by the sidebar radio."""

from __future__ import annotations

from app.llm import normalize_backend


def test_normalize_backend_passthrough() -> None:
    assert normalize_backend("openai") == "openai"
    assert normalize_backend("cloudera") == "cloudera"
    assert normalize_backend("anthropic") == "anthropic"


def test_normalize_backend_maps_display_labels() -> None:
    # Stale session state from an older UI (or format_func confusion) can
    # store the human label instead of the option key.
    assert normalize_backend("OpenAI-compatible") == "openai"
    assert normalize_backend("Cloudera AI Inference") == "cloudera"
    assert normalize_backend("Anthropic") == "anthropic"


def test_normalize_backend_falls_back_for_unknown() -> None:
    assert normalize_backend(None) == "openai"
    assert normalize_backend("nope") == "openai"
    assert normalize_backend(42) == "openai"
