"""Tools tab — declare tool instances users can attach to agents and tasks."""

from __future__ import annotations

import streamlit as st

from .. import cas
from ..i18n import t
from ..models import Design, ToolConfig
from ..tools_catalog import (
    CATALOG,
    CUSTOM_TOOL_KIND,
    ToolCatalogEntry,
    by_kind,
    is_custom,
    is_unresolved,
    kinds,
)


def render(design: Design) -> None:
    st.header(t("tools.header"))
    _list_existing(design)
    st.divider()
    add_form(design)


def _list_existing(design: Design) -> None:
    if not design.tools:
        st.info(t("tools.no_tools"))
        return
    # Auto-expand any tool imported from YAML so the user sees the picker
    # immediately rather than having to guess why exports are blocked.
    unresolved_count = sum(1 for t_ in design.tools if is_unresolved(t_.kind))
    if unresolved_count:
        st.warning(t("tools.unresolved_summary", count=unresolved_count))

    empty_custom = sum(
        1
        for t_ in design.tools
        if is_custom(t_.kind) and not (t_.python_code and t_.python_code.strip())
    )
    if empty_custom:
        st.warning(t("tools.custom_summary", count=empty_custom))

    for i, tool in enumerate(design.tools):
        entry = by_kind(tool.kind)
        unresolved = is_unresolved(tool.kind)
        custom = is_custom(tool.kind)
        needs_attention = unresolved or (
            custom and not (tool.python_code and tool.python_code.strip())
        )
        label = entry.label if entry else (t("tools.unresolved_label") if unresolved else tool.kind)
        icon = "⚠️" if needs_attention else "🔧"
        with st.expander(f"{icon} {tool.name} — {label}", expanded=needs_attention):
            edit_form(design, i, tool, entry)


def edit_form(
    design: Design,
    index: int,
    tool: ToolConfig,
    entry: ToolCatalogEntry | None,
    *,
    scope: str = "tab",
) -> None:
    """Edit an existing tool. ``scope`` keeps keys unique vs the Canvas tab."""
    unresolved = is_unresolved(tool.kind)

    with st.form(key=f"{scope}_tool_edit_{index}"):
        # Kind picker only appears for imported-but-unresolved tools. Users
        # who added a tool through the normal Add form don't need it — the
        # kind is fixed once set.
        new_kind = tool.kind
        if unresolved:
            st.warning(t("tools.unknown_kind_warning"))
            new_kind = st.selectbox(
                t("tools.kind"),
                options=kinds(),
                format_func=lambda k: next(
                    (c.label for c in CATALOG if c.kind == k), k
                ),
                key=f"{scope}_tool_kind_picker_{index}",
            )
            # Reflect the picker's current choice so param inputs render.
            entry = by_kind(new_kind)

        params = dict(tool.params)
        python_code = tool.python_code
        requirements = tool.requirements

        if is_custom(new_kind) or (entry and entry.kind == CUSTOM_TOOL_KIND):
            st.caption(t("tools.custom_hint"))
            # Empty custom tools get the CAS scaffold so the editor matches
            # Agent Studio's create-tool starting point.
            code_value = tool.python_code or cas.custom_tool_scaffold(tool.name)
            reqs_value = (
                tool.requirements
                if tool.requirements is not None
                else cas.custom_tool_requirements_scaffold()
            )
            if not (tool.python_code and tool.python_code.strip()):
                st.info(t("tools.custom_scaffold_notice"))
            python_code = st.text_area(
                t("tools.custom_python_code"),
                value=code_value,
                height=360,
            )
            requirements = st.text_area(
                t("tools.custom_requirements"),
                value=reqs_value,
                height=120,
            )
        elif entry:
            st.caption(entry.description)
            for spec in entry.params:
                params[spec.name] = _param_input(spec, params.get(spec.name))
        elif not unresolved:
            # Should never happen in a well-formed design — surface it.
            st.warning(f"Unknown tool kind {tool.kind!r}")

        col_save, col_delete = st.columns([3, 1])
        with col_save:
            saved = st.form_submit_button(t("common.save"), type="primary")
        with col_delete:
            deleted = st.form_submit_button(t("tools.delete"))

        if saved:
            if is_custom(new_kind):
                design.tools[index] = ToolConfig(
                    name=tool.name,
                    kind=new_kind,
                    params={},
                    python_code=python_code,
                    requirements=requirements,
                )
            else:
                design.tools[index] = ToolConfig(
                    name=tool.name, kind=new_kind, params=params
                )
            st.session_state["design"] = design
            st.rerun()
        if deleted:
            # Also strip references to this tool from every agent and task.
            name = tool.name
            for a in design.agents:
                a.tools = [x for x in a.tools if x != name]
            for tk in design.tasks:
                tk.tools = [x for x in tk.tools if x != name]
            del design.tools[index]
            st.session_state["design"] = design
            st.rerun()


def add_form(design: Design, *, scope: str = "tab") -> None:
    """Add-tool form. ``scope`` keeps keys unique across Canvas + Tools tabs.

    Kind sits outside the form so choosing Custom (CAS) immediately shows the
    CAS ``tool.py`` / ``requirements.txt`` scaffold (Streamlit forms do not
    re-render mid-edit when the selectbox is inside the form).
    """
    st.subheader(t("tools.add"))
    kind = st.selectbox(
        t("tools.kind"),
        options=kinds(),
        format_func=lambda k: next(
            (c.label for c in CATALOG if c.kind == k), k
        ),
        key=f"{scope}_tool_add_kind",
    )
    entry = by_kind(kind)

    # Seed / clear CAS scaffold editors when the kind flips to/from CustomTool.
    code_key = f"{scope}_add_custom_python"
    req_key = f"{scope}_add_custom_reqs"
    seeded_key = f"{scope}_add_custom_seeded"
    if is_custom(kind):
        if not st.session_state.get(seeded_key):
            st.session_state[code_key] = cas.custom_tool_scaffold("custom_tool")
            st.session_state[req_key] = cas.custom_tool_requirements_scaffold()
            st.session_state[seeded_key] = True
    else:
        st.session_state.pop(seeded_key, None)
        st.session_state.pop(code_key, None)
        st.session_state.pop(req_key, None)

    with st.form(key=f"{scope}_tool_add"):
        name = st.text_input(t("tools.name"))
        params: dict[str, object] = {}
        python_code = ""
        requirements = ""

        if is_custom(kind):
            st.caption(t("tools.custom_hint"))
            st.info(t("tools.custom_scaffold_notice"))
            python_code = st.text_area(
                t("tools.custom_python_code"),
                value=st.session_state.get(
                    code_key, cas.custom_tool_scaffold("custom_tool")
                ),
                height=360,
            )
            requirements = st.text_area(
                t("tools.custom_requirements"),
                value=st.session_state.get(
                    req_key, cas.custom_tool_requirements_scaffold()
                ),
                height=120,
            )
        elif entry:
            st.caption(entry.description)
            for spec in entry.params:
                params[spec.name] = _param_input(spec, None)

        if st.form_submit_button(t("tools.add"), type="primary"):
            if not name.strip():
                st.error(t("common.required"))
                return
            if not name.isidentifier():
                st.error(f"{name!r} is not a valid identifier")
                return
            if name in design.tool_names():
                st.error(f"tool {name!r} already exists")
                return
            if is_custom(kind):
                code = (python_code or "").strip()
                # If the user left the default scaffold, retarget the docstring
                # name to the identifier they chose.
                default_scaffold = cas.custom_tool_scaffold("custom_tool").strip()
                if not code or code == default_scaffold:
                    code = cas.custom_tool_scaffold(name.strip())
                reqs = (
                    requirements
                    if requirements is not None
                    else cas.custom_tool_requirements_scaffold()
                )
                design.tools.append(
                    ToolConfig(
                        name=name.strip(),
                        kind=kind,
                        python_code=code,
                        requirements=reqs,
                    )
                )
                st.session_state.pop(seeded_key, None)
                st.session_state.pop(code_key, None)
                st.session_state.pop(req_key, None)
            else:
                design.tools.append(
                    ToolConfig(name=name.strip(), kind=kind, params=params)
                )
            st.session_state["design"] = design
            st.rerun()


def _param_input(spec, current):
    """Render the right Streamlit input for a tool param spec."""
    label = f"{spec.name} ({'required' if spec.required else 'optional'})"
    if spec.type == "bool":
        return st.checkbox(label, value=bool(current), help=spec.help)
    if spec.type == "int":
        return st.number_input(label, value=int(current or 0), help=spec.help)
    return st.text_input(label, value=str(current) if current is not None else "", help=spec.help)
