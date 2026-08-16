from datetime import datetime
from itertools import cycle

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

# Okabe-Ito, colour-blind safe. Machines take these in config order and wrap
# around if there are more machines than colours.
PALETTE = (
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#009E73",  # green
    "#CC79A7",  # reddish purple
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
)
FALLBACK_COLOR = "#555555"  # anything unnamed, including combined 'total' spikes


def assign_colors(machines) -> dict:
    """{machine: colour} in the order given, cycling the palette."""
    return {machine: color for machine, color in zip(machines, cycle(PALETTE))}


def render_power_graph(
    series_by_machine: dict, spike_points: list, out_path: str, title: str,
    labels: dict | None = None, colors: dict | None = None,
) -> str:
    labels = labels or {}
    colors = colors if colors is not None else assign_colors(series_by_machine)
    fig, ax = plt.subplots(figsize=(6, 3.5), dpi=150)

    for machine, points in series_by_machine.items():
        if not points:
            continue
        times = [datetime.fromtimestamp(t) for t, _ in points]
        watts = [w for _, w in points]
        ax.plot(
            times, watts,
            linewidth=2, color=colors.get(machine, FALLBACK_COLOR),
            label=labels.get(machine, machine), solid_capstyle="round",
        )

    for machine, ts, watts in spike_points:
        ax.scatter(
            [datetime.fromtimestamp(ts)], [watts],
            color=colors.get(machine, FALLBACK_COLOR),
            edgecolor="white", zorder=5, s=48,
        )

    ax.set_ylabel("Estimated draw (W)")
    ax.set_title(title, fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linewidth=0.5, alpha=0.3)
    if any(series_by_machine.values()):
        ax.legend(loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
