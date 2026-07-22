"""Agents tab — list, add, edit, and delete agents in the design.

The `add_form` and `edit_form` helpers are also imported by the Canvas
tab (Phase 2 of the workflow-designer roadmap) so users can add and edit
agents from a canvas dialog without switching tabs. Same forms, two
entry points.
"""

from __future__ import annotations

import streamlit as st

from ..i18n import t
from ..models import Agent, Design


def render(design: Design) -> None:
    st.header(t("agents.header"))

    _list_existing(design)
    st.divider()
    add_form(design)


def _list_existing(design: Design) -> None:
    if not design.agents:
        st.info(t("agents.no_agents"))
        return
    for i, agent in enumerate(design.agents):
        with st.expander(f"👤 {agent.name} — {agent.role}", expanded=False):
            edit_form(design, i, agent)


def edit_form(
    design: Design, index: int, agent: Agent, *, scope: str = "tab"
) -> None:
    """Edit an existing agent.

    ``scope`` disambiguates Streamlit widget keys when the same form is
    mounted from both the Agents tab and the Canvas tab in one run.
    """
    with st.form(key=f"{scope}_agent_edit_{index}"):
        role = st.text_input(t("agents.role"), value=agent.role)
        goal = st.text_area(t("agents.goal"), value=agent.goal, height=80)
        backstory = st.text_area(t("agents.backstory"), value=agent.backstory, height=120)

        col1, col2 = st.columns(2)
        with col1:
            llm = st.text_input(t("agents.llm"), value=agent.llm or "")
            max_iter = st.number_input(t("agents.max_iter"), min_value=1, value=agent.max_iter)
            max_retry_limit = st.number_input(
                t("agents.max_retry_limit"), min_value=0, value=agent.max_retry_limit
            )
            date_format = st.text_input(t("agents.date_format"), value=agent.date_format)
        with col2:
            max_rpm = st.number_input(
                t("agents.max_rpm"), min_value=0, value=agent.max_rpm or 0
            )
            verbose = st.checkbox(t("agents.verbose"), value=agent.verbose)
            allow_delegation = st.checkbox(
                t("agents.allow_delegation"), value=agent.allow_delegation
            )
            reasoning = st.checkbox(t("agents.reasoning"), value=agent.reasoning)
            multimodal = st.checkbox(t("agents.multimodal"), value=agent.multimodal)
            respect_ctx = st.checkbox(
                t("agents.respect_context_window"), value=agent.respect_context_window
            )
            inject_date = st.checkbox(t("agents.inject_date"), value=agent.inject_date)

        tools = st.multiselect(
            t("agents.tools"),
            options=design.tool_names(),
            default=[t for t in agent.tools if t in design.tool_names()],
        )

        col_save, col_delete = st.columns([3, 1])
        with col_save:
            saved = st.form_submit_button(t("common.save"), type="primary")
        with col_delete:
            deleted = st.form_submit_button(t("agents.delete"))

        if saved:
            design.agents[index] = Agent(
                name=agent.name,
                role=role.strip(),
                goal=goal.strip(),
                backstory=backstory.strip(),
                llm=llm.strip() or None,
                tools=tools,
                max_iter=int(max_iter),
                max_rpm=int(max_rpm) or None,
                verbose=verbose,
                allow_delegation=allow_delegation,
                reasoning=reasoning,
                multimodal=multimodal,
                respect_context_window=respect_ctx,
                max_retry_limit=int(max_retry_limit),
                inject_date=inject_date,
                date_format=date_format,
            )
            st.session_state["design"] = design
            st.rerun()
        if deleted:
            del design.agents[index]
            st.session_state["design"] = design
            st.rerun()


def add_form(design: Design, *, scope: str = "tab") -> None:
    """Add-agent form. ``scope`` keeps keys unique across Canvas + Agents tabs."""
    st.subheader(t("agents.add"))

    # AI assist block — populates the form defaults via session state.
    assist_key = f"{scope}_agents_add_assist"
    client = st.session_state.get("llm_client")

    with st.expander(t("agents.assist"), expanded=False):
        if client is None:
            st.warning(t("assist.no_llm"))
        else:
            st.caption(t("agents.assist.help"))
            prompt = st.text_input(
                t("agents.assist.prompt"), key=f"{assist_key}_prompt"
            )
            if st.button(t("agents.assist"), key=f"{assist_key}_btn") and prompt.strip():
                try:
                    draft = client.draft_agent(prompt.strip(), st.session_state.get("lang", "en"))
                    st.session_state[f"{assist_key}_role"] = draft.role
                    st.session_state[f"{assist_key}_goal"] = draft.goal
                    st.session_state[f"{assist_key}_backstory"] = draft.backstory
                    st.rerun()
                except Exception as e:
                    st.error(t("assist.failed", error=str(e)))

    with st.form(key=f"{scope}_agent_add"):
        name = st.text_input(t("agents.name"))
        role = st.text_input(
            t("agents.role"), value=st.session_state.get(f"{assist_key}_role", "")
        )
        goal = st.text_area(
            t("agents.goal"), value=st.session_state.get(f"{assist_key}_goal", ""), height=80
        )
        backstory = st.text_area(
            t("agents.backstory"),
            value=st.session_state.get(f"{assist_key}_backstory", ""),
            height=120,
        )
        tools = st.multiselect(t("agents.tools"), options=design.tool_names())

        if st.form_submit_button(t("agents.add"), type="primary"):
            if not name.strip() or not role.strip() or not goal.strip() or not backstory.strip():
                st.error(t("common.required"))
                return
            if not name.isidentifier():
                st.error(f"{name!r} is not a valid identifier")
                return
            if name in design.agent_names():
                st.error(f"agent {name!r} already exists")
                return
            design.agents.append(
                Agent(
                    name=name.strip(),
                    role=role.strip(),
                    goal=goal.strip(),
                    backstory=backstory.strip(),
                    tools=tools,
                )
            )
            # Clear assist buffer so the next add starts fresh.
            for k in (
                f"{assist_key}_role",
                f"{assist_key}_goal",
                f"{assist_key}_backstory",
                f"{assist_key}_prompt",
            ):
                st.session_state.pop(k, None)
            st.session_state["design"] = design
            st.rerun()
