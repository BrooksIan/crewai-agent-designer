"""Tasks tab — list, add, edit, and delete tasks."""

from __future__ import annotations

import streamlit as st

from ..i18n import t
from ..models import Design, Task


def render(design: Design) -> None:
    st.header(t("tasks.header"))
    _list_existing(design)
    st.divider()
    add_form(design)


def _list_existing(design: Design) -> None:
    if not design.tasks:
        st.info(t("tasks.no_tasks"))
        return
    for i, task in enumerate(design.tasks):
        title = f"📋 {task.name}"
        if task.agent:
            title += f" → {task.agent}"
        with st.expander(title, expanded=False):
            edit_form(design, i, task)


def edit_form(
    design: Design, index: int, task: Task, *, scope: str = "tab"
) -> None:
    """Edit an existing task. ``scope`` keeps keys unique vs the Canvas tab."""
    with st.form(key=f"{scope}_task_edit_{index}"):
        description = st.text_area(t("tasks.description"), value=task.description, height=120)
        expected_output = st.text_area(
            t("tasks.expected_output"), value=task.expected_output, height=120
        )

        agent_options = [""] + design.agent_names()
        agent = st.selectbox(
            t("tasks.agent"),
            options=agent_options,
            index=agent_options.index(task.agent) if task.agent in agent_options else 0,
        )

        tools = st.multiselect(
            t("tasks.tools"),
            options=design.tool_names(),
            default=[x for x in task.tools if x in design.tool_names()],
        )

        # Context — dependencies on other tasks. Exclude self to avoid the
        # obvious self-loop.
        other_task_names = [n for n in design.task_names() if n != task.name]
        context = st.multiselect(
            t("tasks.context"),
            options=other_task_names,
            default=[x for x in task.context if x in other_task_names],
        )

        col1, col2 = st.columns(2)
        with col1:
            async_execution = st.checkbox(
                t("tasks.async_execution"), value=task.async_execution
            )
            human_input = st.checkbox(t("tasks.human_input"), value=task.human_input)
        with col2:
            markdown = st.checkbox(t("tasks.markdown"), value=task.markdown)
            output_file = st.text_input(t("tasks.output_file"), value=task.output_file or "")

        col_save, col_delete = st.columns([3, 1])
        with col_save:
            saved = st.form_submit_button(t("common.save"), type="primary")
        with col_delete:
            deleted = st.form_submit_button(t("tasks.delete"))

        if saved:
            design.tasks[index] = Task(
                name=task.name,
                description=description.strip(),
                expected_output=expected_output.strip(),
                agent=agent or None,
                tools=tools,
                context=context,
                async_execution=async_execution,
                human_input=human_input,
                markdown=markdown,
                output_file=output_file.strip() or None,
            )
            st.session_state["design"] = design
            st.rerun()
        if deleted:
            del design.tasks[index]
            # If this task was in the crew's task_order, drop it.
            design.crew.task_order = [n for n in design.crew.task_order if n != task.name]
            st.session_state["design"] = design
            st.rerun()


def add_form(design: Design, *, scope: str = "tab") -> None:
    """Add-task form. ``scope`` keeps keys unique across Canvas + Tasks tabs."""
    st.subheader(t("tasks.add"))

    assist_key = f"{scope}_tasks_add_assist"
    client = st.session_state.get("llm_client")

    with st.expander(t("tasks.assist"), expanded=False):
        if client is None:
            st.warning(t("assist.no_llm"))
        else:
            st.caption(t("tasks.assist.help"))
            prompt = st.text_input(t("tasks.assist.prompt"), key=f"{assist_key}_prompt")
            if st.button(t("tasks.assist"), key=f"{assist_key}_btn") and prompt.strip():
                try:
                    draft = client.draft_task(prompt.strip(), st.session_state.get("lang", "en"))
                    st.session_state[f"{assist_key}_description"] = draft.description
                    st.session_state[f"{assist_key}_expected"] = draft.expected_output
                    st.rerun()
                except Exception as e:
                    st.error(t("assist.failed", error=str(e)))

    with st.form(key=f"{scope}_task_add"):
        name = st.text_input(t("tasks.name"))
        description = st.text_area(
            t("tasks.description"),
            value=st.session_state.get(f"{assist_key}_description", ""),
            height=100,
        )
        expected_output = st.text_area(
            t("tasks.expected_output"),
            value=st.session_state.get(f"{assist_key}_expected", ""),
            height=100,
        )
        agent = st.selectbox(t("tasks.agent"), options=[""] + design.agent_names())
        tools = st.multiselect(t("tasks.tools"), options=design.tool_names())

        if st.form_submit_button(t("tasks.add"), type="primary"):
            if not name.strip() or not description.strip() or not expected_output.strip():
                st.error(t("common.required"))
                return
            if not name.isidentifier():
                st.error(f"{name!r} is not a valid identifier")
                return
            if name in design.task_names():
                st.error(f"task {name!r} already exists")
                return
            design.tasks.append(
                Task(
                    name=name.strip(),
                    description=description.strip(),
                    expected_output=expected_output.strip(),
                    agent=agent or None,
                    tools=tools,
                )
            )
            for k in (
                f"{assist_key}_description",
                f"{assist_key}_expected",
                f"{assist_key}_prompt",
            ):
                st.session_state.pop(k, None)
            st.session_state["design"] = design
            st.rerun()
