"""Helpers for Streamlit download buttons that ship CAS / CrewAI zips.

Streamlit reruns the script when a ``download_button`` is clicked. If the
``data=`` payload changes between the click and the next render (e.g.
``cas_workflow.to_cas_workflow_zip`` mints a fresh UUID every call), the
browser download can fail or receive empty/corrupt bytes. These helpers
cache the last zip for the current design fingerprint so the bytes stay
stable across that rerun.
"""

from __future__ import annotations

import hashlib
import re

import streamlit as st

from .. import cas_workflow, generate
from ..models import Design


def _fingerprint(design: Design) -> str:
    return hashlib.sha256(design.model_dump_json().encode("utf-8")).hexdigest()


def safe_zip_filename(stem: str, *, suffix: str = ".zip") -> str:
    """Sanitize a crew name into a downloadable zip filename."""
    raw = (stem or "crew").strip().lower() or "crew"
    cleaned = re.sub(r"[^a-z0-9._-]+", "_", raw).strip("._") or "crew"
    if not cleaned.endswith(suffix):
        cleaned = f"{cleaned}{suffix}"
    return cleaned


def cas_workflow_zip_bytes(design: Design) -> bytes:
    """Return CAS workflow zip bytes, cached until ``design`` changes."""
    fp = _fingerprint(design)
    if st.session_state.get("_cas_zip_fp") != fp:
        st.session_state["_cas_zip_fp"] = fp
        st.session_state["_cas_zip_bytes"] = cas_workflow.to_cas_workflow_zip(design)
    return st.session_state["_cas_zip_bytes"]


def crewai_zip_bytes(design: Design) -> bytes:
    """Return CrewAI project zip bytes, cached until ``design`` changes."""
    fp = _fingerprint(design)
    if st.session_state.get("_crewai_zip_fp") != fp:
        st.session_state["_crewai_zip_fp"] = fp
        st.session_state["_crewai_zip_bytes"] = generate.to_zip(design)
    return st.session_state["_crewai_zip_bytes"]
