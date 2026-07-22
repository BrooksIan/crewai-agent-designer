"""Generate tab — create a full crew Design from a natural-language prompt."""

from __future__ import annotations

import streamlit as st

from .. import cas, design_from_prompt, llm, validate
from ..i18n import t
from ..models import Design
from .zip_download import cas_workflow_zip_bytes, safe_zip_filename


_EXAMPLES_EN = [
    "Research a market opportunity and write a brief with citations.",
    "Ingest PDFs, extract key fields, and draft a compliance summary.",
    "Monitor a website for product changes and alert on price drops.",
]

_EXAMPLES_ES = [
    "Investigar una oportunidad de mercado y redactar un informe con citas.",
    "Procesar PDFs, extraer campos clave y redactar un resumen de cumplimiento.",
    "Monitorear un sitio web en busca de cambios de producto y alertar por bajadas de precio.",
]


def render(design: Design) -> None:
    st.header(t("generate.header"))
    st.caption(t("generate.hint"))

    client = st.session_state.get("llm_client")
    if client is None:
        st.warning(t("assist.no_llm"))

    lang = st.session_state.get("lang", "en")
    examples = _EXAMPLES_ES if lang == "es" else _EXAMPLES_EN

    st.caption(t("generate.examples"))
    cols = st.columns(len(examples))
    for i, (col, example) in enumerate(zip(cols, examples)):
        with col:
            if st.button(example, key=f"generate_example_{i}", use_container_width=True):
                st.session_state["generate_prompt"] = example
                st.rerun()

    prompt = st.text_area(
        t("generate.prompt"),
        key="generate_prompt",
        height=140,
        placeholder=t("generate.prompt_placeholder"),
    )

    can_run = bool(client) and bool((prompt or "").strip())
    if st.button(
        t("generate.button"),
        type="primary",
        disabled=not can_run,
        use_container_width=True,
    ):
        assert client is not None
        try:
            with st.spinner(t("generate.spinner")):
                draft = client.draft_design(prompt.strip(), lang)
                result = design_from_prompt.from_draft(draft)
        except (llm.LLMError, design_from_prompt.AssembleError) as e:
            st.session_state["generate_error"] = str(e)
            st.session_state.pop("pending_generate_result", None)
            st.rerun()
            return

        st.session_state.pop("generate_error", None)
        st.session_state["pending_generate_result"] = result
        if not _design_is_non_empty(design):
            _apply_generate(result)
            st.rerun()

    error = st.session_state.get("generate_error")
    if error:
        st.error(t("generate.failure", error=error))

    pending = st.session_state.get("pending_generate_result")
    if pending is not None and _design_is_non_empty(st.session_state["design"]):
        # Only show confirm when we haven't applied yet (pending still set
        # and design still the old non-empty one). After apply, pending is cleared.
        st.warning(t("generate.confirm_replace"))
        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                t("sidebar.import.replace_yes"),
                type="primary",
                use_container_width=True,
                key="generate_replace_yes",
            ):
                _apply_generate(pending)
                st.rerun()
        with c2:
            if st.button(
                t("sidebar.import.replace_cancel"),
                use_container_width=True,
                key="generate_replace_cancel",
            ):
                st.session_state.pop("pending_generate_result", None)
                st.rerun()

    success = st.session_state.get("generate_success")
    if success:
        st.success(
            t(
                "generate.success",
                n_agents=success["n_agents"],
                n_tasks=success["n_tasks"],
                n_tools=success["n_tools"],
            )
        )
        st.info(t("generate.open_canvas"))

        warnings = st.session_state.get("generate_warnings") or []
        if warnings:
            st.caption(t("generate.warnings_header"))
            for w in warnings:
                st.warning(w)

        followups = st.session_state.get("generate_followups") or []
        if followups:
            st.caption(t("generate.followups_header"))
            for msg in followups:
                st.caption(f"• {msg}")

        current = st.session_state["design"]
        cas_errors = validate.validate(current, target="cas_workflow")
        if cas_errors:
            st.warning(t("generate.cas_blocked"))
            for err in cas_errors:
                st.write(f"- **`{err.where}`** — {err.message}")
        elif current.agents:
            try:
                zip_bytes = cas_workflow_zip_bytes(current)
            except cas.CasExportError as e:
                st.error(t("export.zip_failed", error=str(e)))
                return
            except Exception as e:  # noqa: BLE001
                st.error(t("export.zip_failed", error=str(e)))
                return
            filename = safe_zip_filename(
                f"{current.crew.name}_cas_workflow", suffix=".zip"
            )
            st.download_button(
                label=t("generate.download_cas"),
                data=zip_bytes,
                file_name=filename,
                mime="application/zip",
                type="primary",
                key="generate_cas_download",
            )
            st.caption(t("generate.cas_hint"))


def _design_is_non_empty(design: Design) -> bool:
    return bool(design.agents or design.tasks or design.tools)


def _apply_generate(result: design_from_prompt.AssembleResult) -> None:
    st.session_state["design"] = result.design
    st.session_state["current_design_name"] = ""
    st.session_state["generate_warnings"] = list(result.warnings)
    st.session_state["generate_success"] = {
        "n_agents": len(result.design.agents),
        "n_tasks": len(result.design.tasks),
        "n_tools": len(result.design.tools),
    }
    # Soft CrewAI follow-ups (e.g. polish) — do not block apply.
    followups = [
        f"{e.where}: {e.message}"
        for e in validate.validate(result.design, target="crewai")
    ]
    st.session_state["generate_followups"] = followups
    st.session_state.pop("pending_generate_result", None)
    st.session_state.pop("generate_error", None)
