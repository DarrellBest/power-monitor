import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from power_monitor import config, db, discord_notify, graph, reports  # noqa: E402

SPIKE_CONTEXT_SECONDS = 900


def main(cfg=None):
    cfg = cfg or config.load_config()
    machines = cfg.machine_names
    reports.prune_old_reports(cfg.reports_dir, retention_days=cfg.report_retention_days)
    conn = db.get_connection(cfg.db_path)
    events = db.get_unalerted_spikes(conn)
    if not events:
        return

    colors = graph.assign_colors(machines)
    for event in events:
        machine = event["machine"]
        event_ts = event["ts"]

        series = {}
        for m in machines:
            rows = db.get_recent_samples(conn, m, event_ts - SPIKE_CONTEXT_SECONDS)
            series[m] = [
                (r["ts"], (r["cpu_watts"] or 0) + (r["gpu_watts"] or 0))
                for r in rows
                if r["cpu_watts"] is not None or r["gpu_watts"] is not None
            ]

        out_path = f"{cfg.reports_dir}/spike_{event['id']}.png"
        graph.render_power_graph(
            series, [(machine, event_ts, event["draw_watts"])], out_path,
            title="Power spike: combined total",
            labels=cfg.labels, colors=colors,
        )

        lines = [
            "Combined power spike detected: total estimated draw across all machines hit "
            f"{event['draw_watts']:.0f}W, "
            f"{event['delta_watts']:.0f}W over the "
            f"{cfg.spike_threshold_watts:.0f}W threshold "
            "(estimated CPU+GPU only, not true wall watts, not the UPS total).",
            "Per-machine estimated draw at that moment:",
        ]
        for m in machines:
            row = db.get_sample_near(conn, m, event_ts, tolerance=30.0)
            label = cfg.label_for(m)
            if row is None:
                lines.append(f"  {label}: no reading")
                continue
            watts = (row["cpu_watts"] or 0) + (row["gpu_watts"] or 0)
            lines.append(f"  {label}: {watts:.0f}W")
        if event["top_gpu_process"]:
            lines.append(f"Top GPU process at the time: {event['top_gpu_process']}")
        if event["top_cpu_process"]:
            lines.append(f"Top CPU process at the time: {event['top_cpu_process']}")

        discord_notify.send_message(
            "\n".join(lines), image_path=out_path, env_path=cfg.discord_env_path
        )
        db.mark_spike_alerted(conn, event["id"])


if __name__ == "__main__":
    main()
