import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from power_monitor import config, cost, db, discord_notify, graph, reports, rollup  # noqa: E402

LOOKBACK_SECONDS = 604800
MAX_SPIKES_LISTED = 10


def format_spike(row, cfg) -> str:
    """One spike line. 'total' rows are combined all-machine events over the threshold;
    anything else is a legacy per-machine row still sitting in spike_events."""
    label = cfg.label_for(row["machine"])
    if row["machine"] == config.TOTAL_MACHINE:
        return (
            f"{label}: {row['draw_watts']:.0f}W total "
            f"(+{row['delta_watts']:.0f}W over threshold)"
        )
    return f"{label}: +{row['delta_watts']:.0f}W to {row['draw_watts']:.0f}W"


def format_per_machine_kwh(by_machine, cfg) -> str:
    """'My Server 12.1 / Gaming PC 15.0 kWh' — the compact monthly breakdown."""
    parts = [
        f"{cfg.label_for(m)} {by_machine.get(m, 0.0):.1f}" for m in cfg.machine_names
    ]
    return " / ".join(parts) + " kWh"


def main(cfg=None):
    cfg = cfg or config.load_config()
    machines = cfg.machine_names
    reports.prune_old_reports(cfg.reports_dir, retention_days=cfg.report_retention_days)
    conn = db.get_connection(cfg.db_path)
    now_ts = time.time()
    since_ts = now_ts - LOOKBACK_SECONDS
    # Summarize any hours that completed since the collector last rolled up, so the
    # trend sections below never read stale hourly data.
    rollup.rollup_complete_hours(
        conn, machines, now_ts, retention_days=cfg.hourly_retention_days
    )

    series = {}
    for m in machines:
        rows = db.get_recent_samples(conn, m, since_ts)
        series[m] = [
            (r["ts"], (r["cpu_watts"] or 0) + (r["gpu_watts"] or 0))
            for r in rows
            if r["cpu_watts"] is not None or r["gpu_watts"] is not None
        ]

    weeks_spikes = conn.execute(
        "SELECT * FROM spike_events WHERE ts >= ? ORDER BY ts ASC", (since_ts,)
    ).fetchall()
    spike_points = [(r["machine"], r["ts"], r["draw_watts"]) for r in weeks_spikes]

    out_path = f"{cfg.reports_dir}/weekly_{int(now_ts)}.png"
    graph.render_power_graph(
        series, spike_points, out_path, title="Last 7 days estimated power draw",
        labels=cfg.labels, colors=graph.assign_colors(machines),
    )

    lines = [
        "Power monitor weekly summary (estimated CPU+GPU draw per machine, "
        "not true wall watts, not the UPS total):"
    ]
    for m in machines:
        lines.append(
            f"- {cfg.label_for(m)}: {len(series[m])} samples collected in the last 7 days"
        )

    this_week = rollup.summarize_range(conn, machines, since_ts, now_ts)
    last_week = rollup.summarize_range(
        conn, machines, since_ts - LOOKBACK_SECONDS, since_ts
    )

    lines.append("")
    lines.append("Estimated electricity cost this week:")
    total_kwh = 0.0
    for m in machines:
        kwh = cost.energy_kwh(series[m])
        total_kwh += kwh
        # avg is kWh-weighted over the hours hourly_summary actually covers; both
        # fall back to the raw week's series if there's no hourly data yet.
        avg = this_week[m]["avg_watts"]
        peak = this_week[m]["peak_watts"]
        if avg is None and series[m]:
            avg = sum(w for _, w in series[m]) / len(series[m])
        if peak is None and series[m]:
            peak = max(w for _, w in series[m])
        detail = f" (avg {avg:.0f}W, peak {peak:.0f}W)" if avg is not None else ""
        lines.append(
            f"- {cfg.label_for(m)}: {kwh:.1f} kWh, "
            f"${cost.cost_usd(kwh, cfg.rate_per_kwh):.2f}{detail}"
        )
    lines.append(
        f"Total: {total_kwh:.1f} kWh, ${cost.cost_usd(total_kwh, cfg.rate_per_kwh):.2f} "
        f"(@ ${cfg.rate_per_kwh:.3f}/kWh)"
    )

    # This week's figure is the raw-sample total above, so the two lines agree; the
    # prior week comes from hourly_summary, which outlives the raw-sample retention.
    week_kwh = total_kwh
    prev_week_kwh = sum(last_week[m]["kwh"] for m in machines)
    if prev_week_kwh > 0:
        change = (week_kwh - prev_week_kwh) / prev_week_kwh * 100.0
        lines.append(
            f"vs last week: ${cost.cost_usd(prev_week_kwh, cfg.rate_per_kwh):.2f} → "
            f"${cost.cost_usd(week_kwh, cfg.rate_per_kwh):.2f} ({change:+.1f}%)"
        )
    else:
        lines.append("vs last week: no prior-week data yet")
    lines.append("")

    monthly = rollup.monthly_kwh(conn, machines)
    this_month = monthly.get(rollup.month_key(now_ts), {})
    mtd_kwh = sum(this_month.get(m, 0.0) for m in machines)
    lines.append("Monthly (local calendar):")
    lines.append(
        f"- {time.strftime('%B', time.localtime(now_ts))} to date: {mtd_kwh:.1f} kWh, "
        f"${cost.cost_usd(mtd_kwh, cfg.rate_per_kwh):.2f} "
        f"({format_per_machine_kwh(this_month, cfg)})"
    )
    complete = rollup.complete_months(conn)
    if complete:
        mean = {
            m: sum(monthly[k].get(m, 0.0) for k in complete) / len(complete)
            for m in machines
        }
        mean_total = sum(mean.values())
        lines.append(
            f"- Average complete month ({len(complete)} so far): {mean_total:.1f} kWh, "
            f"${cost.cost_usd(mean_total, cfg.rate_per_kwh):.2f} "
            f"({format_per_machine_kwh(mean, cfg)})"
        )
    else:
        lines.append("- Average monthly: collecting history (no complete months yet)")
    lines.append("")

    if weeks_spikes:
        lines.append(f"{len(weeks_spikes)} spike(s) detected in the last 7 days:")
        biggest = sorted(weeks_spikes, key=lambda r: r["delta_watts"], reverse=True)
        for r in biggest[:MAX_SPIKES_LISTED]:
            lines.append(f"- {format_spike(r, cfg)}")
        if len(weeks_spikes) > MAX_SPIKES_LISTED:
            lines.append(
                f"…and {len(weeks_spikes) - MAX_SPIKES_LISTED} more spike(s) this week."
            )
    else:
        lines.append("No spikes detected on any monitored machine in the last 7 days.")

    discord_notify.send_message(
        "\n".join(lines), image_path=out_path, env_path=cfg.discord_env_path
    )


if __name__ == "__main__":
    main()
