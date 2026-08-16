import os

from power_monitor import graph

SERIES = {
    "server": [(1000.0, 60.0), (1010.0, 65.0), (1020.0, 300.0)],
    "desktop": [(1000.0, 130.0), (1010.0, 131.0), (1020.0, 129.0)],
    "gpu-box": [],
}


def test_render_power_graph_creates_file(tmp_path):
    out_path = str(tmp_path / "test_graph.png")

    result = graph.render_power_graph(
        SERIES, [("server", 1020.0, 300.0)], out_path, title="Test graph",
        labels={"server": "My Server", "desktop": "Gaming PC"},
    )

    assert result == out_path
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 0


def test_render_power_graph_handles_all_empty_series(tmp_path):
    out_path = str(tmp_path / "empty.png")
    result = graph.render_power_graph(
        {"server": [], "desktop": []}, [], out_path, title="Empty"
    )
    assert os.path.exists(result)


def test_render_power_graph_needs_no_labels_or_colors(tmp_path):
    out_path = str(tmp_path / "bare.png")
    assert os.path.exists(
        graph.render_power_graph(SERIES, [], out_path, title="Bare")
    )


def test_assign_colors_follows_machine_order():
    colors = graph.assign_colors(["a", "b", "c"])
    assert colors["a"] == graph.PALETTE[0]
    assert colors["b"] == graph.PALETTE[1]
    assert colors["c"] == graph.PALETTE[2]


def test_assign_colors_wraps_around_for_many_machines():
    machines = [f"m{i}" for i in range(len(graph.PALETTE) + 2)]
    colors = graph.assign_colors(machines)
    assert len(colors) == len(machines)
    assert colors[machines[len(graph.PALETTE)]] == graph.PALETTE[0]


def test_unknown_machines_including_total_use_the_fallback_color(tmp_path):
    """Spike rows are recorded under 'total', which is never a configured machine."""
    colors = graph.assign_colors(["server"])
    assert colors.get("total", graph.FALLBACK_COLOR) == graph.FALLBACK_COLOR
    out_path = str(tmp_path / "total.png")
    assert os.path.exists(
        graph.render_power_graph(
            SERIES, [("total", 1020.0, 1500.0)], out_path, title="Total spike",
            colors=colors,
        )
    )
