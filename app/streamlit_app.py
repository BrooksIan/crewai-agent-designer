"""Streamlit entry point for the CrewAI Agent Designer.

Wires the sidebar (language toggle, LLM backend status, YAML/CAS import,
design save/load) and the eight tabs. All state lives in ``st.session_state``
— chiefly:

- ``design`` : the current `Design` model being edited
- ``lang``   : ``"en"`` or ``"es"``
- ``llm_client`` : an `LLMClient` instance (or None)

Run locally:
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

# Streamlit runs this file as a top-level script (`streamlit run
# app/streamlit_app.py`), which strips it of package context and breaks
# relative imports from ``.tabs``, ``.models``, etc. Prepend the project
# root to ``sys.path`` so we can import ``app`` as a package instead.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # noqa: E402

from app import import_cas, import_yaml, llm, storage  # noqa: E402
from app.i18n import t  # noqa: E402
from app.models import Design  # noqa: E402
from app.tabs import agents as agents_tab  # noqa: E402
from app.tabs import canvas as canvas_tab  # noqa: E402
from app.tabs import crew as crew_tab  # noqa: E402
from app.tabs import export as export_tab  # noqa: E402
from app.tabs import generate as generate_tab  # noqa: E402
from app.tabs import preview as preview_tab  # noqa: E402
from app.tabs import tasks as tasks_tab  # noqa: E402
from app.tabs import tools as tools_tab  # noqa: E402
from app.tabs import workplaces as workplaces_tab  # noqa: E402


def _init_state() -> None:
    """Seed session_state on first render, once per session."""
    if "lang" not in st.session_state:
        st.session_state["lang"] = "en"
    if "design" not in st.session_state:
        st.session_state["design"] = Design()
    if "llm_client" not in st.session_state:
        # None is a valid state — the UI shows a hint and disables Assist.
        st.session_state["llm_client"] = llm.build_client()
    if "current_design_name" not in st.session_state:
        st.session_state["current_design_name"] = ""
    if "current_workplace" not in st.session_state:
        st.session_state["current_workplace"] = None


def _llm_settings() -> None:
    """Render the collapsible LLM API configuration block.

    Lets the user pick a backend, enter base URL / API key / model, apply the
    result to ``st.session_state["llm_client"]``, and test connectivity with a
    minimal ``ping`` request. Values persist in session state under
    ``llm_form_*`` keys so the form remembers what the user typed even after
    switching tabs.
    """
    client = st.session_state.get("llm_client")

    # Header + one-line status.
    if client is None:
        st.warning(t("sidebar.llm.none"))
    else:
        st.success(client.label)

    with st.expander(t("sidebar.llm.settings"), expanded=client is None):
        # Backend radio — pre-selected from an env-detected backend on first
        # render, then whatever the user last picked. Normalize first: stale
        # session state may hold a display label ("OpenAI-compatible") from
        # an older build, which is not a valid option key.
        # Seed / coerce session state *before* the widget; do not also pass
        # ``index=`` (Streamlit warns when both Session State and a default
        # are supplied for the same keyed widget).
        options = ["cloudera", "openai", "anthropic"]
        st.session_state["llm_form_backend"] = llm.normalize_backend(
            st.session_state.get("llm_form_backend")
            or llm.detect_backend_from_env()
        )
        backend_labels = {
            "cloudera": t("sidebar.llm.cloudera"),
            "openai": t("sidebar.llm.openai"),
            "anthropic": t("sidebar.llm.anthropic"),
        }
        backend = st.radio(
            t("sidebar.llm.backend_choice"),
            options=options,
            format_func=lambda o: backend_labels[o],
            key="llm_form_backend",
            horizontal=False,
        )

        # Seed base_url / model from the selected backend's defaults the first
        # time the user picks it — but never overwrite what they've typed.
        defaults = llm.BACKEND_DEFAULTS[backend]
        base_url_key = f"llm_form_base_url_{backend}"
        model_key = f"llm_form_model_{backend}"
        api_key_key = f"llm_form_api_key_{backend}"
        if base_url_key not in st.session_state:
            st.session_state[base_url_key] = defaults["base_url"]
        if model_key not in st.session_state:
            st.session_state[model_key] = defaults["model"]

        st.text_input(t("sidebar.llm.base_url"), key=base_url_key)
        st.text_input(
            t("sidebar.llm.api_key"),
            key=api_key_key,
            type="password",
            help="Stored in session state only — never written to disk.",
        )
        st.text_input(t("sidebar.llm.model"), key=model_key)

        col_apply, col_test = st.columns(2)
        with col_apply:
            if st.button(t("sidebar.llm.apply"), use_container_width=True, type="primary"):
                try:
                    new_client = llm.build_from_config(
                        backend,
                        base_url=st.session_state[base_url_key],
                        api_key=st.session_state[api_key_key],
                        model=st.session_state[model_key],
                    )
                    st.session_state["llm_client"] = new_client
                    st.success(t("sidebar.llm.applied", label=new_client.label))
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
        with col_test:
            if st.button(t("sidebar.llm.test"), use_container_width=True):
                client = st.session_state.get("llm_client")
                if client is None:
                    st.warning(t("sidebar.llm.no_client"))
                else:
                    try:
                        reply = client.ping()
                        st.success(t("sidebar.llm.test_ok", reply=reply))
                    except Exception as e:
                        st.error(t("sidebar.llm.test_fail", error=str(e)))


def _design_is_non_empty(design: Design) -> bool:
    """True when the design has anything a user might not want overwritten."""
    return bool(design.agents or design.tasks or design.tools)


def _import_yaml_block() -> None:
    """Render the "Import CrewAI YAML" sidebar section.

    Two uploaders (agents.yaml and tasks.yaml) + an Import button. The
    button is enabled only when both files are attached. If the current
    design has content, a confirm-replace dialog blocks the actual
    replacement until the user acknowledges — same shape as the "New
    design" button does.
    """
    with st.expander(t("sidebar.import.header"), expanded=False):
        agents_file = st.file_uploader(
            t("sidebar.import.agents_upload"),
            type=["yaml", "yml"],
            key="import_agents_file",
        )
        tasks_file = st.file_uploader(
            t("sidebar.import.tasks_upload"),
            type=["yaml", "yml"],
            key="import_tasks_file",
        )
        both_uploaded = agents_file is not None and tasks_file is not None

        if st.button(
            t("sidebar.import.button"),
            use_container_width=True,
            type="primary",
            disabled=not both_uploaded,
        ):
            try:
                agents_text = agents_file.read().decode("utf-8")
                tasks_text = tasks_file.read().decode("utf-8")
                result = import_yaml.from_yaml(agents_text, tasks_text)
            except import_yaml.ImportError as e:
                st.session_state["import_error"] = str(e)
                st.rerun()
                return
            # Stash the result and decide whether we need confirmation.
            st.session_state["pending_import_result"] = result
            st.session_state.pop("import_error", None)
            if not _design_is_non_empty(st.session_state["design"]):
                _apply_import(result)
                st.rerun()

        # Persisted parse error from the last button click.
        error = st.session_state.get("import_error")
        if error:
            st.error(t("sidebar.import.failure", error=error))

        # Confirm-replace flow. Shown as an inline warning + two buttons
        # so the user can back out without losing the parsed result.
        pending = st.session_state.get("pending_import_result")
        if pending is not None and _design_is_non_empty(st.session_state["design"]):
            st.warning(t("sidebar.import.confirm_replace"))
            c1, c2 = st.columns(2)
            with c1:
                if st.button(
                    t("sidebar.import.replace_yes"),
                    use_container_width=True,
                    type="primary",
                    key="import_replace_yes",
                ):
                    _apply_import(pending)
                    st.rerun()
            with c2:
                if st.button(
                    t("sidebar.import.replace_cancel"),
                    use_container_width=True,
                    key="import_replace_cancel",
                ):
                    st.session_state.pop("pending_import_result", None)
                    st.rerun()


def _apply_import(result: import_yaml.ImportResult | import_cas.ImportResult) -> None:
    """Commit an ImportResult into session_state and stash warnings.

    Called from two places (fresh-design short-circuit + confirm-yes),
    hence broken out. Clears any pending prompt so a subsequent import
    starts with a clean slate. Shared by YAML and CAS importers.
    """
    st.session_state["design"] = result.design
    st.session_state["current_design_name"] = ""  # force Save-as on the next save
    st.session_state["import_warnings"] = list(result.warnings)
    st.session_state["import_success"] = {
        "n_agents": len(result.design.agents),
        "n_tasks": len(result.design.tasks),
    }
    st.session_state.pop("pending_import_result", None)
    st.session_state.pop("pending_cas_import_result", None)
    st.session_state.pop("import_error", None)
    st.session_state.pop("cas_import_error", None)


def _import_cas_block() -> None:
    """Render the "Import CAS template" sidebar section.

    Single zip uploader + Import button, with the same confirm-replace
    flow as YAML import when the current design already has content.
    """
    with st.expander(t("sidebar.cas_import.header"), expanded=False):
        st.caption(t("sidebar.cas_import.hint"))
        zip_file = st.file_uploader(
            t("sidebar.cas_import.upload"),
            type=["zip"],
            key="import_cas_zip_file",
        )

        if st.button(
            t("sidebar.cas_import.button"),
            use_container_width=True,
            type="primary",
            disabled=zip_file is None,
            key="import_cas_button",
        ):
            try:
                result = import_cas.from_zip(zip_file.read())
            except import_cas.ImportError as e:
                st.session_state["cas_import_error"] = str(e)
                st.rerun()
                return
            st.session_state["pending_cas_import_result"] = result
            st.session_state.pop("cas_import_error", None)
            if not _design_is_non_empty(st.session_state["design"]):
                _apply_import(result)
                st.rerun()

        error = st.session_state.get("cas_import_error")
        if error:
            st.error(t("sidebar.cas_import.failure", error=error))

        pending = st.session_state.get("pending_cas_import_result")
        if pending is not None and _design_is_non_empty(st.session_state["design"]):
            st.warning(t("sidebar.import.confirm_replace"))
            c1, c2 = st.columns(2)
            with c1:
                if st.button(
                    t("sidebar.import.replace_yes"),
                    use_container_width=True,
                    type="primary",
                    key="cas_import_replace_yes",
                ):
                    _apply_import(pending)
                    st.rerun()
            with c2:
                if st.button(
                    t("sidebar.import.replace_cancel"),
                    use_container_width=True,
                    key="cas_import_replace_cancel",
                ):
                    st.session_state.pop("pending_cas_import_result", None)
                    st.rerun()


def _sidebar() -> None:
    with st.sidebar:
        # One-click language toggle at the top — the label is the *other*
        # language so the button text tells you where you're going.
        if st.button(t("sidebar.language.toggle"), use_container_width=True):
            st.session_state["lang"] = "es" if st.session_state["lang"] == "en" else "en"
            st.rerun()

        st.divider()

        _llm_settings()

        st.divider()

        _import_yaml_block()
        _import_cas_block()
        # Surface success + any warnings from the last successful import.
        success = st.session_state.pop("import_success", None)
        if success:
            st.success(t("sidebar.import.success", **success))
        warnings = st.session_state.pop("import_warnings", None)
        if warnings:
            st.caption(t("sidebar.import.warnings_header"))
            for w in warnings:
                st.warning(w)

        st.divider()

        st.caption(t("sidebar.designs"))
        existing = storage.list_designs()

        # Open — pick a saved design and load it into session state.
        if existing:
            selected = st.selectbox(
                t("sidebar.open"),
                options=[""] + existing,
                index=(existing.index(st.session_state["current_design_name"]) + 1)
                if st.session_state["current_design_name"] in existing
                else 0,
                key="sidebar_open_select",
            )
            if selected and selected != st.session_state["current_design_name"]:
                try:
                    st.session_state["design"] = storage.load(selected)
                    st.session_state["current_design_name"] = selected
                    st.rerun()
                except storage.StorageError as e:
                    st.error(str(e))

        # Save current design.
        name_input = st.text_input(
            t("sidebar.design_name"),
            value=st.session_state["current_design_name"],
            key="sidebar_save_name",
        )
        col1, col2 = st.columns(2)
        with col1:
            if st.button(t("sidebar.save"), use_container_width=True):
                if not name_input.strip():
                    st.error(t("common.required"))
                else:
                    try:
                        storage.save(name_input.strip(), st.session_state["design"])
                        st.session_state["current_design_name"] = name_input.strip()
                        st.success("saved")
                    except storage.StorageError as e:
                        st.error(str(e))
        with col2:
            if st.button(t("sidebar.new"), use_container_width=True):
                st.session_state["design"] = Design()
                st.session_state["current_design_name"] = ""
                st.rerun()

        # Delete current design (only if we're editing one loaded from disk).
        current = st.session_state["current_design_name"]
        if current and current in existing:
            if st.button(
                f"{t('sidebar.delete')}: {current}",
                use_container_width=True,
                type="secondary",
            ):
                storage.delete(current)
                st.session_state["current_design_name"] = ""
                st.session_state["design"] = Design()
                st.rerun()

        st.divider()

        # Workplace selector and info
        st.caption(t("sidebar.workplace.header"))
        workplaces = storage.list_workplaces()
        current_wp = st.session_state["current_workplace"]

        if workplaces:
            # Show current workplace
            display_options = [t("sidebar.workplace.unassigned")] + workplaces
            current_index = (
                (workplaces.index(current_wp) + 1)
                if current_wp and current_wp in workplaces
                else 0
            )
            selected_wp = st.selectbox(
                t("sidebar.workplace.current"),
                options=display_options,
                index=current_index,
                key="workplace_selector",
            )
            # Update session state if changed
            new_wp = None if selected_wp == t("sidebar.workplace.unassigned") else selected_wp
            if new_wp != current_wp:
                st.session_state["current_workplace"] = new_wp
                st.rerun()

            # Show workplace info if one is selected
            if new_wp:
                try:
                    wp = storage.load_workplace(new_wp)
                    designs_in_wp = storage.designs_in_workplace(new_wp)
                    st.caption(f"📊 {len(designs_in_wp)} design(s)")
                    if wp.description:
                        st.caption(f"ℹ️ {wp.description}")
                except storage.StorageError:
                    pass
        else:
            st.info(t("sidebar.workplace.none"))


def main() -> None:
    st.set_page_config(page_title="CrewAI Agent Designer", page_icon="🤖", layout="wide")
    _init_state()

    st.title(t("app.title"))
    st.caption(t("app.tagline"))

    _sidebar()

    design = st.session_state["design"]

    # Canvas is the first tab — it's the new landing view for non-developer
    # users (Phase 1 of the workflow-designer roadmap: read-only visual DAG
    # of the current design). The form-based tabs stay as detail editors.
    tabs = st.tabs([
        t("tabs.canvas"),
        t("tabs.generate"),
        t("tabs.agents"),
        t("tabs.tasks"),
        t("tabs.tools"),
        t("tabs.crew"),
        t("tabs.preview"),
        t("tabs.export"),
        t("sidebar.workplace.header"),
    ])

    with tabs[0]:
        canvas_tab.render(design)
    with tabs[1]:
        generate_tab.render(design)
    with tabs[2]:
        agents_tab.render(design)
    with tabs[3]:
        tasks_tab.render(design)
    with tabs[4]:
        tools_tab.render(design)
    with tabs[5]:
        crew_tab.render(design)
    with tabs[6]:
        preview_tab.render(design)
    with tabs[7]:
        export_tab.render(design)
    with tabs[8]:
        workplaces_tab.render()


if __name__ == "__main__":
    main()
