"""Preview tab — read-only view of the artifact the current export target will produce.

Mirrors the Export tab's target selection so users can inspect what they're
about to download without leaving the page. The CrewAI preview shows the
three generated files (`agents.yaml`, `tasks.yaml`, `crew.py`); the CAS
preview shows the workflow JSON.
"""

from __future__ import annotations

import json

import streamlit as st

from .. import cas_workflow, generate
from ..i18n import t
from ..models import Design


def render(design: Design) -> None:
    st.header(t("preview.header"))
    if not design.agents or not design.tasks:
        st.info(t("preview.empty"))
        return

    # Follow the Export tab's target so both tabs stay in sync. Default to
    # CrewAI when nothing has been selected yet.
    target = st.session_state.get("export_target", "crewai")

    if target == "cas_workflow":
        _render_cas_workflow_preview(design)
    else:
        _render_crewai_preview(design)


def _render_crewai_preview(design: Design) -> None:
    st.subheader(t("preview.agents_yaml"))
    st.code(generate.to_agents_yaml(design), language="yaml")

    st.subheader(t("preview.tasks_yaml"))
    st.code(generate.to_tasks_yaml(design), language="yaml")

    st.subheader(t("preview.crew_py"))
    st.code(generate.to_crew_py(design), language="python")


def _render_cas_workflow_preview(design: Design) -> None:
    payload = cas_workflow.to_cas_workflow_json(design)
    st.subheader(t("preview.workflow_json"))
    # Indented, sorted-keys-preserved JSON — matches what CAS will read.
    st.code(json.dumps(payload, indent=2), language="json")
