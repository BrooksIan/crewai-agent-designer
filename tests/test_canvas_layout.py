"""Tests for canvas node position layout helpers."""

from __future__ import annotations

from app.graph import Node, layout_positions


def test_layout_positions_puts_kinds_in_separate_columns() -> None:
    nodes = [
        Node(id="tool:t", kind="tool", label="t"),
        Node(id="agent:a", kind="agent", label="a"),
        Node(id="task:x", kind="task", label="x"),
    ]
    pos = layout_positions(nodes)
    assert pos["tool:t"][0] < pos["agent:a"][0] < pos["task:x"][0]


def test_layout_positions_spreads_peers_vertically() -> None:
    nodes = [
        Node(id="agent:a1", kind="agent", label="a1"),
        Node(id="agent:a2", kind="agent", label="a2"),
        Node(id="agent:a3", kind="agent", label="a3"),
    ]
    pos = layout_positions(nodes)
    ys = [pos[n.id][1] for n in nodes]
    assert ys == sorted(ys)
    assert ys[1] - ys[0] >= 100
    assert ys[2] - ys[1] >= 100


def test_layout_positions_is_deterministic() -> None:
    nodes = [
        Node(id="task:t2", kind="task", label="t2"),
        Node(id="task:t1", kind="task", label="t1"),
        Node(id="agent:a", kind="agent", label="a"),
    ]
    assert layout_positions(nodes) == layout_positions(nodes)


def test_empty_nodes_yield_empty_positions() -> None:
    assert layout_positions([]) == {}
