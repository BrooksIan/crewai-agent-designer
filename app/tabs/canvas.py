"""Canvas tab — visual read-only view of the current `Design`.

Phase 1 of the workflow-designer roadmap: users get a live diagram of
their design without any edit-through-canvas plumbing yet. Later phases
add click-to-edit, drag-to-connect, and delete-through-canvas. This tab
is where all of that will land; today it just renders.

Library selection is decided at import time. Priority order:

1. **``streamlit-flow-component``** (React Flow wrapper). Modern, fast,
   has hooks for drag / connect / delete that Phases 2+ will use.
2. **``streamlit-agraph``** (vis.js wrapper). Fallback when the flow
   component isn't installable — pure visualization, no interaction.
3. **Text listing** if neither is importable. Users still see the
   projection, just not as a graph.

The tab reads ``st.session_state["design"]`` (populated by every other
tab and by YAML import) and calls :func:`app.graph.design_to_graph`.
Zero state lives in this tab — pure derived view.
"""

from __future__ import annotations

import streamlit as st

from .. import canvas_actions, graph
from ..i18n import t
from ..models import Design
from . import agents as agents_tab
from . import tasks as tasks_tab
from . import tools as tools_tab


# ---------------------------------------------------------------------------
# Library detection — done at import so a missing dep never breaks the app
# ---------------------------------------------------------------------------

try:
    from streamlit_flow import streamlit_flow
    from streamlit_flow.elements import StreamlitFlowEdge, StreamlitFlowNode
    from streamlit_flow.layouts import ManualLayout
    from streamlit_flow.state import StreamlitFlowState

    _HAS_FLOW = True
except Exception:  # pragma: no cover — depends on runtime installability
    _HAS_FLOW = False

try:
    from streamlit_agraph import Config as AgraphConfig
    from streamlit_agraph import Edge as AgraphEdge
    from streamlit_agraph import Node as AgraphNode
    from streamlit_agraph import agraph

    _HAS_AGRAPH = True
except Exception:  # pragma: no cover
    _HAS_AGRAPH = False


# ---------------------------------------------------------------------------
# Node styling — colors that read distinctly on Streamlit's default theme
# ---------------------------------------------------------------------------

# Chosen to be color-blind friendly (deuteranopia-safe pairs from the
# Wong palette). Kept in one place so the two rendering backends agree.
_NODE_COLORS: dict[str, dict[str, str]] = {
    "agent":   {"bg": "#0072B2", "fg": "#FFFFFF"},  # blue
    "task":    {"bg": "#E69F00", "fg": "#000000"},  # amber
    "tool":    {"bg": "#009E73", "fg": "#FFFFFF"},  # green
    "manager": {"bg": "#CC79A7", "fg": "#FFFFFF"},  # rose (synthetic node)
}


def render(design: Design) -> None:
    """Top-level renderer for the Canvas tab.

    Layout (top → bottom):

    1. **Toolbar** — three Add buttons (agent / task / tool) that open
       the corresponding tab's ``add_form``. Available even on an empty
       design so users can build from scratch here.
    2. **Legend** — color key for the four node kinds.
    3. **Delete-selected** button (only after a node is selected on the
       canvas). Dispatches to :mod:`app.canvas_actions` so
       cross-references get scrubbed.
    4. **Canvas** — the graph itself.
    5. **Edit panel** — beneath the graph, shows the selected node's
       edit form (reused from the corresponding tab) so users don't
       have to switch tabs. Phase 3 upgrades this to a modal dialog
       via st.dialog once the flow component's event model supports
       distinct double-click.
    """
    st.header(t("canvas.header"))

    _render_toolbar(design)

    nodes, edges = graph.design_to_graph(design)

    if not nodes:
        st.info(t("canvas.empty"))
        return

    _render_legend()
    _render_selected_delete_button(design)

    if _HAS_FLOW:
        _render_with_flow(nodes, edges)
    elif _HAS_AGRAPH:
        _render_with_agraph(nodes, edges)
    else:
        _render_text_fallback(nodes, edges)

    _render_selected_edit_panel(design)


def _render_toolbar(design: Design) -> None:
    """Toolbar for adding agent / task / tool / manager from the canvas.

    Agent/task/tool each open a popover with the corresponding tab form.
    Manager toggles hierarchical process (+ manager LLM) — the manager
    node is synthesized by the graph projection, not stored as an Agent.
    """
    col_a, col_t, col_l, col_m, col_clear = st.columns([1, 1, 1, 1, 1])
    with col_a:
        with st.popover(t("canvas.add.agent"), use_container_width=True):
            agents_tab.add_form(design, scope="canvas")
    with col_t:
        with st.popover(t("canvas.add.task"), use_container_width=True):
            tasks_tab.add_form(design, scope="canvas")
    with col_l:
        with st.popover(t("canvas.add.tool"), use_container_width=True):
            tools_tab.add_form(design, scope="canvas")
    with col_m:
        with st.popover(t("canvas.add.manager"), use_container_width=True):
            _render_manager_form(design, scope="toolbar")
    with col_clear:
        _render_clear_button(design)


def _render_clear_button(design: Design) -> None:
    """Clear the whole canvas (agents / tasks / tools / crew defaults)."""
    has_content = bool(design.agents or design.tasks or design.tools)
    if st.button(
        t("canvas.clear"),
        use_container_width=True,
        disabled=not has_content,
        key="canvas_clear",
    ):
        if has_content:
            st.session_state["canvas_clear_confirm"] = True

    if st.session_state.get("canvas_clear_confirm") and has_content:
        st.warning(t("canvas.clear.confirm"))
        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                t("canvas.clear.confirm_yes"),
                type="primary",
                use_container_width=True,
                key="canvas_clear_yes",
            ):
                canvas_actions.clear_design(design)
                st.session_state["design"] = design
                st.session_state["current_design_name"] = ""
                st.session_state.pop("canvas_clear_confirm", None)
                st.session_state.pop("canvas_selected_node_id", None)
                st.session_state.pop("canvas_flow_state", None)
                # Drop zip caches so Export doesn't serve a stale bundle.
                st.session_state.pop("_cas_zip_fp", None)
                st.session_state.pop("_cas_zip_bytes", None)
                st.session_state.pop("_crewai_zip_fp", None)
                st.session_state.pop("_crewai_zip_bytes", None)
                st.session_state.pop("generate_success", None)
                st.rerun()
        with c2:
            if st.button(
                t("canvas.clear.confirm_no"),
                use_container_width=True,
                key="canvas_clear_no",
            ):
                st.session_state.pop("canvas_clear_confirm", None)
                st.rerun()


def _render_manager_form(design: Design, *, scope: str = "toolbar") -> None:
    """Popover/panel body for enabling or updating the synthetic manager."""
    already = design.crew.process == "hierarchical"
    st.caption(t("canvas.manager.help"))
    with st.form(key=f"canvas_manager_form_{scope}"):
        manager_llm = st.text_input(
            t("crew.manager_llm"),
            value=design.crew.manager_llm or "",
            key=f"canvas_manager_llm_{scope}",
        )
        saved = st.form_submit_button(
            t("canvas.manager.update") if already else t("canvas.manager.enable"),
            type="primary",
        )
        removed = False
        if already:
            removed = st.form_submit_button(t("canvas.manager.remove"))

        if saved:
            if already:
                warnings = canvas_actions.update_manager_llm(design, manager_llm)
            else:
                warnings = canvas_actions.enable_manager(design, manager_llm)
            if warnings:
                for w in warnings:
                    st.error(w)
                return
            st.session_state["design"] = design
            st.session_state.pop("canvas_flow_state", None)
            st.rerun()
        if removed:
            canvas_actions.disable_manager(design)
            st.session_state["design"] = design
            st.session_state.pop("canvas_selected_node_id", None)
            st.session_state.pop("canvas_flow_state", None)
            st.rerun()


def _selected_node_id() -> str | None:
    """Return the currently-selected canvas node id from session state, if any."""
    return st.session_state.get("canvas_selected_node_id")


def _render_selected_delete_button(design: Design) -> None:
    """When a node is selected on the canvas, show a Delete button that
    dispatches to :mod:`app.canvas_actions`.

    Deleting the synthetic manager switches the crew back to sequential.
    """
    node_id = _selected_node_id()
    if not node_id:
        return

    if node_id == "agent:__manager__":
        st.caption(t("canvas.editor_hint.manager"))
        if st.button(
            t("canvas.delete_selected", label="manager"),
            type="secondary",
            key="canvas_delete_manager",
        ):
            canvas_actions.disable_manager(design)
            st.session_state["design"] = design
            st.session_state.pop("canvas_selected_node_id", None)
            st.session_state.pop("canvas_flow_state", None)
            st.rerun()
        return

    kind, name = _split_node_id(node_id)
    if kind is None:
        return

    if st.button(
        t("canvas.delete_selected", label=name),
        type="secondary",
        key=f"canvas_delete_{node_id}",
    ):
        warnings = _dispatch_delete(design, kind, name)
        # Selection is stale — the node no longer exists.
        st.session_state.pop("canvas_selected_node_id", None)
        # Also drop the flow component's cached state so the deleted
        # node doesn't linger in its React tree.
        st.session_state.pop("canvas_flow_state", None)
        st.session_state["canvas_delete_warnings"] = warnings
        st.rerun()

    # Show cleanup notes from the most recent delete.
    prior = st.session_state.pop("canvas_delete_warnings", None)
    if prior is not None:
        if prior:
            for w in prior:
                st.warning(w)
        else:
            st.success(t("canvas.delete_done"))


def _render_selected_edit_panel(design: Design) -> None:
    """Below the graph, render the edit form for the currently-selected
    node so users can tweak it without switching tabs.

    Streamlit forms handle their own submit-and-rerun, so once the user
    saves, the design updates and the canvas repaints on the next render.
    """
    node_id = _selected_node_id()
    if not node_id:
        return

    if node_id == "agent:__manager__":
        st.divider()
        st.subheader(t("canvas.edit_selected", label="manager"))
        _render_manager_form(design, scope="selected")
        return

    kind, name = _split_node_id(node_id)
    if kind is None:
        return

    st.divider()
    st.subheader(t("canvas.edit_selected", label=name))
    if kind == "agent":
        idx = _find_index(design.agents, name)
        if idx is not None:
            agents_tab.edit_form(design, idx, design.agents[idx], scope="canvas")
    elif kind == "task":
        idx = _find_index(design.tasks, name)
        if idx is not None:
            tasks_tab.edit_form(design, idx, design.tasks[idx], scope="canvas")
    elif kind == "tool":
        idx = _find_index(design.tools, name)
        if idx is not None:
            from ..tools_catalog import by_kind

            tools_tab.edit_form(
                design,
                idx,
                design.tools[idx],
                by_kind(design.tools[idx].kind),
                scope="canvas",
            )


def _split_node_id(node_id: str) -> tuple[str | None, str | None]:
    """Parse ``kind:name`` back into components. Returns ``(None, None)``
    on unknown formats — the canvas emits stable IDs but we're defensive
    since the flow component's state can carry stale values across reruns."""
    if ":" not in node_id:
        return None, None
    kind, _, name = node_id.partition(":")
    if kind not in ("agent", "task", "tool"):
        return None, None
    return kind, name


def _dispatch_delete(design: Design, kind: str, name: str) -> list[str]:
    if kind == "agent":
        return canvas_actions.delete_agent(design, name)
    if kind == "task":
        return canvas_actions.delete_task(design, name)
    if kind == "tool":
        return canvas_actions.delete_tool(design, name)
    return []


def _find_index(items: list, name: str) -> int | None:
    for i, item in enumerate(items):
        if item.name == name:
            return i
    return None


def _render_legend() -> None:
    """Small color-key strip above the graph so users know what each node
    kind means. Uses inline HTML for the swatches — Streamlit's native
    widgets don't have a compact 4-item legend primitive."""
    cols = st.columns(4)
    for col, kind in zip(cols, ("agent", "task", "tool", "manager")):
        c = _NODE_COLORS[kind]
        col.markdown(
            f"<div style='display:flex;align-items:center;gap:0.5em;'>"
            f"<span style='display:inline-block;width:0.9em;height:0.9em;"
            f"background:{c['bg']};border-radius:0.15em;'></span>"
            f"<span style='font-size:0.9em;'>{t(f'canvas.legend.{kind}')}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# streamlit-flow-component renderer (interactive-capable; Phase 1 uses it
# read-only)
# ---------------------------------------------------------------------------


def _render_with_flow(nodes: list, edges: list) -> None:
    """Render via ``streamlit-flow-component``.

    Phase 1 uses ``draggable=False`` and ``connectable=False`` — the
    canvas is read-only. Phase 2 will flip these on. We're already
    emitting on_click hooks so users get feedback when they interact:
    a selected node's summary shows below the graph.

    Positions come from :func:`graph.layout_positions` (tool / agent /
    task columns) rather than ELK tree layout, which stacks peers for
    this DAG shape.
    """
    positions = graph.layout_positions(nodes)
    flow_nodes = [
        StreamlitFlowNode(
            id=n.id,
            pos=positions[n.id],
            data={"content": n.label},
            node_type="default",
            source_position="right",
            target_position="left",
            style={
                "background": _NODE_COLORS[n.kind]["bg"],
                "color": _NODE_COLORS[n.kind]["fg"],
                "border": "1px solid rgba(0,0,0,0.2)",
                "borderRadius": "6px",
                "padding": "8px 12px",
                "fontWeight": "500",
                "minWidth": "120px",
            },
            draggable=False,
            connectable=False,
            selectable=True,
        )
        for n in nodes
    ]
    flow_edges = [
        StreamlitFlowEdge(
            id=e.id,
            source=e.source,
            target=e.target,
            label=e.label,
            edge_type="smoothstep",
            animated=(e.kind == "task_context"),  # visually cue data flow
        )
        for e in edges
    ]

    # Session-scoped flow state so React Flow keeps its internal layout
    # cache across reruns. Keying by the design lets us reset the state
    # when the user opens a new design without stale nodes lingering.
    state_key = "canvas_flow_state"
    st.session_state[state_key] = StreamlitFlowState(flow_nodes, flow_edges)

    # Grow the viewport with the tallest column so nothing looks crushed.
    tallest = max(
        (sum(1 for n in nodes if n.kind == k) for k in ("tool", "agent", "task", "manager")),
        default=1,
    )
    height = max(500, 160 + tallest * 120)

    selected = streamlit_flow(
        key="canvas_flow",
        state=st.session_state[state_key],
        height=height,
        fit_view=True,
        show_controls=True,
        show_minimap=True,
        layout=ManualLayout(),
        get_node_on_click=True,
        pan_on_drag=True,
    )

    _render_selected_node_summary(selected, nodes)


def _render_selected_node_summary(state, nodes: list) -> None:
    """When a node is clicked, remember the selection in session state
    (so the delete button + edit panel below the graph can find it) and
    show a short summary above them.
    """
    selected_id = None
    if state is not None:
        # StreamlitFlowState exposes the selected node id after a click.
        selected_id = getattr(state, "selected_id", None)

    # Persist selection across reruns. Reset only when the user
    # explicitly clicks empty space (state returns None).
    if selected_id:
        st.session_state["canvas_selected_node_id"] = selected_id
    elif state is not None:
        # State came back but no node selected → clear.
        st.session_state.pop("canvas_selected_node_id", None)

    active_id = _selected_node_id()
    if not active_id:
        st.caption(t("canvas.hint.click_node"))
        return
    node = next((n for n in nodes if n.id == active_id), None)
    if node is None:
        return

    tab_hint_key = f"canvas.editor_hint.{node.kind}"
    st.subheader(t("canvas.selected.title", label=node.label))
    st.caption(t(tab_hint_key))
    # Small key-value dump of the node's carried data.
    if node.data:
        for k, v in node.data.items():
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "…"
            st.text(f"{k}: {v}")


# ---------------------------------------------------------------------------
# streamlit-agraph fallback (read-only viz)
# ---------------------------------------------------------------------------


def _render_with_agraph(nodes: list, edges: list) -> None:
    """Simple viz via ``streamlit-agraph``. Loses interactivity but
    still shows the graph structure — useful if the primary library
    fails to build in a restricted runtime."""
    agraph_nodes = [
        AgraphNode(
            id=n.id,
            label=n.label,
            size=25,
            color=_NODE_COLORS[n.kind]["bg"],
            font={"color": _NODE_COLORS[n.kind]["fg"], "size": 13},
        )
        for n in nodes
    ]
    agraph_edges = [
        AgraphEdge(source=e.source, target=e.target, label=e.label)
        for e in edges
    ]
    config = AgraphConfig(
        width=800,
        height=500,
        directed=True,
        physics=True,
        hierarchical=False,
    )
    agraph(nodes=agraph_nodes, edges=agraph_edges, config=config)


# ---------------------------------------------------------------------------
# Text fallback — always available, doesn't need any graph library
# ---------------------------------------------------------------------------


def _render_text_fallback(nodes: list, edges: list) -> None:
    """No graph library available — render the projection as a table.

    Ships value even in the most stripped-down deployment. Users still
    see the wiring, just without visual layout.
    """
    st.warning(t("canvas.no_lib_warning"))
    st.subheader("Nodes")
    for n in nodes:
        st.markdown(f"- **{n.label}** _(kind: `{n.kind}`)_")
    st.subheader("Edges")
    for e in edges:
        st.markdown(f"- `{e.source}` → `{e.target}` _({e.kind})_")
