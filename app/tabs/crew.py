"""Crew tab — top-level orchestration settings and task ordering."""

from __future__ import annotations

import streamlit as st

from ..i18n import t
from ..models import Crew, Design


def render(design: Design) -> None:
    st.header(t("crew.header"))
    with st.form(key="crew_form"):
        name = st.text_input(t("crew.name"), value=design.crew.name, help=t("crew.name.help"))
        process = st.selectbox(
            t("crew.process"),
            options=["sequential", "hierarchical"],
            index=0 if design.crew.process == "sequential" else 1,
            format_func=lambda p: t(f"crew.process.{p}"),
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            verbose = st.checkbox(t("crew.verbose"), value=design.crew.verbose)
        with col2:
            memory = st.checkbox(t("crew.memory"), value=design.crew.memory)
        with col3:
            cache = st.checkbox(t("crew.cache"), value=design.crew.cache)

        manager_llm = ""
        if process == "hierarchical":
            manager_llm = st.text_input(
                t("crew.manager_llm"), value=design.crew.manager_llm or ""
            )

        # Task ordering — reorder via a text area (one name per line) for now.
        # Drag-and-drop needs a component; keep it simple until users ask.
        current_order = design.crew.task_order or design.task_names()
        st.caption(t("crew.task_order.help"))
        order_text = st.text_area(
            t("crew.task_order"),
            value="\n".join(current_order),
            height=120,
        )

        if st.form_submit_button(t("common.save"), type="primary"):
            order_lines = [line.strip() for line in order_text.splitlines() if line.strip()]
            design.crew = Crew(
                name=name.strip() or "MyCrew",
                process=process,
                verbose=verbose,
                memory=memory,
                cache=cache,
                manager_llm=manager_llm.strip() or None,
                task_order=order_lines,
            )
            st.session_state["design"] = design
            st.rerun()
