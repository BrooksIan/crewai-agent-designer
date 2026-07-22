"""Tests for Streamlit zip download helpers (pure bits only)."""

from __future__ import annotations

from app.tabs.zip_download import safe_zip_filename


def test_safe_zip_filename_sanitizes() -> None:
    assert safe_zip_filename("MyCrew") == "mycrew.zip"
    assert safe_zip_filename("Price Watch!!", suffix=".zip") == "price_watch.zip"
    assert safe_zip_filename("MyCrew_cas_workflow") == "mycrew_cas_workflow.zip"
    assert safe_zip_filename("") == "crew.zip"
