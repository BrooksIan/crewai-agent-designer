"""Export tab — validate the design and offer a downloadable zip.

Two targets:

- **CrewAI project** (default) — the runnable `agents.yaml`/`tasks.yaml`/`crew.py`
  bundle produced by ``app.generate``.
- **CAS workflow bundle** — a full Cloudera Agent Studio upload containing
  ``workflow_template.json`` plus every declared tool packaged in the shape
  CAS expects, produced by ``app.cas_workflow``. Ingested by CAS in one
  operation; users compose no further YAML by hand.

Validation runs the same way for both — a broken design can't be downloaded.
The CAS path additionally surfaces "dropped field" warnings for parts of the
design that don't map onto CAS's schema (task context, per-task tools,
async_execution, output_file, etc.) so users can decide whether to keep the
CrewAI export alongside for full fidelity.
"""

from __future__ import annotations

import streamlit as st

from .. import cas, cas_workflow, validate
from ..i18n import t
from ..models import Design
from .zip_download import cas_workflow_zip_bytes, crewai_zip_bytes, safe_zip_filename


def render(design: Design) -> None:
    st.header(t("export.header"))

    # Radio at the top selects the artifact. Not inside a form so switching
    # rebuilds the preview and warnings on the same rerun.
    target = st.radio(
        t("export.target"),
        options=["crewai", "cas_workflow"],
        index=0,
        format_func=lambda o: t(f"export.target.{o}"),
        key="export_target",
        horizontal=True,
    )

    with st.spinner(t("export.validate")):
        errors = validate.validate(design, target=target)

    if errors:
        st.error(t("export.errors"))
        for err in errors:
            st.write(f"- **`{err.where}`** — {err.message}")
        return

    if target == "crewai":
        _render_crewai(design)
    else:
        _render_cas_workflow(design)


def _render_crewai(design: Design) -> None:
    st.success(t("export.valid"))
    try:
        zip_bytes = crewai_zip_bytes(design)
    except Exception as e:  # noqa: BLE001 — surface any generator failure in-UI
        st.error(t("export.zip_failed", error=str(e)))
        return
    filename = safe_zip_filename(design.crew.name)
    st.download_button(
        label=t("export.download"),
        data=zip_bytes,
        file_name=filename,
        mime="application/zip",
        type="primary",
        key="export_crewai_download",
    )


def _render_cas_workflow(design: Design) -> None:
    """CAS branch — validate additionally, warn about dropped fields, offer download."""
    if not design.agents:
        # Not strictly required by the schema, but a workflow with no agents
        # is a workflow that does nothing. Surface it here rather than let
        # CAS reject a technically-valid but useless upload.
        st.warning(t("export.cas.no_agents"))
        return

    # Fields our Design carries that CAS can't preserve — inform, don't block.
    dropped = cas_workflow.warnings_for_dropped_fields(design)
    for msg in dropped:
        st.warning(t("export.cas.dropped_field", detail=msg))

    st.success(t("export.valid"))
    st.caption(t("export.cas.workflow.hint"))

    try:
        zip_bytes = cas_workflow_zip_bytes(design)
    except cas.CasExportError as e:
        st.error(t("export.zip_failed", error=str(e)))
        return
    except Exception as e:  # noqa: BLE001
        st.error(t("export.zip_failed", error=str(e)))
        return

    filename = safe_zip_filename(
        f"{design.crew.name}_cas_workflow", suffix=".zip"
    )
    st.download_button(
        label=t("export.download"),
        data=zip_bytes,
        file_name=filename,
        mime="application/zip",
        type="primary",
        key="export_cas_download",
    )
