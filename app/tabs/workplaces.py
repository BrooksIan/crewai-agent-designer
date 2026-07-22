"""Workplaces tab — organize designs into team/project namespaces.

Allows users to create, list, edit, and delete workplaces, and assign
designs to workplaces for team collaboration and organization.
"""

from __future__ import annotations

import streamlit as st

from ..i18n import t
from .. import storage


def render() -> None:
    """Render the workplaces management interface."""
    st.header(t("sidebar.workplace.header"))

    # Tabs for list view and create/edit
    list_tab, create_tab, manage_tab = st.tabs([
        t("sidebar.workplace.header"),
        t("sidebar.workplace.create"),
        t("sidebar.workplace.assign_design"),
    ])

    with list_tab:
        _render_list()

    with create_tab:
        _render_create()

    with manage_tab:
        _render_manage_designs()


def _render_list() -> None:
    """Display all workplaces with summary info and quick actions."""
    workplaces = storage.list_workplaces()

    if not workplaces:
        st.info(t("sidebar.workplace.none"))
        return

    st.subheader(t("sidebar.workplace.header"))

    for wp_name in workplaces:
        try:
            wp = storage.load_workplace(wp_name)
            designs = storage.designs_in_workplace(wp_name)

            with st.container(border=True):
                col1, col2, col3 = st.columns([2, 3, 1])

                with col1:
                    st.write(f"**{wp.name}**")
                    if wp.description:
                        st.caption(wp.description)

                with col2:
                    st.caption(f"{t('sidebar.workplace.designs_count', count=len(designs))}")
                    if designs:
                        with st.expander("View designs"):
                            for design_name in designs:
                                st.text(f"  • {design_name}")

                with col3:
                    col_edit, col_del = st.columns(2)
                    with col_edit:
                        if st.button("✏️", key=f"edit_{wp_name}", help="Edit"):
                            st.session_state[f"edit_wp_{wp_name}"] = True
                    with col_del:
                        if st.button("🗑️", key=f"del_{wp_name}", help="Delete"):
                            st.session_state[f"delete_wp_{wp_name}"] = True

                # Delete confirmation
                if st.session_state.get(f"delete_wp_{wp_name}"):
                    st.warning(t("common.confirm_delete"))
                    col_yes, col_no = st.columns(2)
                    with col_yes:
                        if st.button(f"Yes, delete '{wp_name}'", key=f"del_yes_{wp_name}"):
                            try:
                                storage.delete_workplace(wp_name)
                                st.session_state.pop(f"delete_wp_{wp_name}", None)
                                st.success(t("sidebar.workplace.success_deleted", name=wp_name))
                                st.rerun()
                            except storage.StorageError as e:
                                st.error(str(e))
                    with col_no:
                        if st.button("Cancel", key=f"del_no_{wp_name}"):
                            st.session_state.pop(f"delete_wp_{wp_name}", None)
                            st.rerun()

                # Edit form (inline)
                if st.session_state.get(f"edit_wp_{wp_name}"):
                    with st.form(key=f"edit_form_{wp_name}"):
                        new_desc = st.text_input(
                            t("sidebar.workplace.description"),
                            value=wp.description,
                        )
                        members_text = st.text_input(
                            t("sidebar.workplace.members"),
                            value=", ".join(wp.members),
                        )
                        if st.form_submit_button(t("common.save")):
                            members = [m.strip() for m in members_text.split(",") if m.strip()]
                            try:
                                storage.update_workplace(wp_name, description=new_desc, members=members)
                                st.session_state.pop(f"edit_wp_{wp_name}", None)
                                st.success(t("sidebar.workplace.success_updated", name=wp_name))
                                st.rerun()
                            except storage.StorageError as e:
                                st.error(str(e))

        except storage.StorageError as e:
            st.error(f"Error loading workplace: {e}")


def _render_create() -> None:
    """Form to create a new workplace."""
    st.subheader(t("sidebar.workplace.dialog.create_title"))

    with st.form(key="create_workplace_form"):
        name = st.text_input(
            t("sidebar.workplace.dialog.name_label"),
            help="Letters, digits, dashes, underscores only (max 64 chars)",
        )
        description = st.text_area(
            t("sidebar.workplace.dialog.description_label"),
            help="Brief description of this team or project",
        )
        members_text = st.text_input(
            t("sidebar.workplace.dialog.members_label"),
            help="Optional: user IDs or emails, comma-separated",
        )

        if st.form_submit_button(t("sidebar.workplace.dialog.create_button"), type="primary"):
            if not name.strip():
                st.error(t("common.required"))
            else:
                members = [m.strip() for m in members_text.split(",") if m.strip()]
                try:
                    storage.create_workplace(name.strip(), description=description.strip())
                    st.success(t("sidebar.workplace.success_created", name=name.strip()))
                    st.rerun()
                except storage.StorageError as e:
                    st.error(t("sidebar.workplace.error_exists", name=name))


def _render_manage_designs() -> None:
    """Assign designs to workplaces."""
    st.subheader(t("sidebar.workplace.assign_design"))

    all_designs = storage.list_designs()
    workplaces = storage.list_workplaces()

    if not all_designs:
        st.info("No designs available to assign.")
        return

    if not workplaces:
        st.warning("Create a workplace first.")
        return

    selected_design = st.selectbox(
        "Select design",
        options=all_designs,
        key="design_to_assign",
    )

    if selected_design:
        design = storage.load(selected_design)

        col1, col2 = st.columns(2)
        with col1:
            selected_workplace = st.selectbox(
                t("sidebar.workplace.current"),
                options=[t("sidebar.workplace.unassigned")] + workplaces,
                index=(
                    (workplaces.index(design.workplace) + 1)
                    if design.workplace and design.workplace in workplaces
                    else 0
                ),
                key="workplace_target",
            )

        with col2:
            st.write("")  # Align button with input
            if st.button("Assign", type="primary", use_container_width=True):
                target = None if selected_workplace == t("sidebar.workplace.unassigned") else selected_workplace
                try:
                    storage.move_design_to_workplace(selected_design, target)
                    st.success(f"Design moved to '{target or 'Unassigned'}'")
                    st.rerun()
                except storage.StorageError as e:
                    st.error(str(e))
