"""Hourly rollup of raw samples into hourly_summary, for long-term history.

Raw samples are pruned after raw_retention_days (db.prune_old_samples); these
one-row-per-machine-per-hour summaries are kept far longer, which is what the
weekly report's week-over-week and monthly sections read from.

Every entry point takes the machine names to work over — the caller passes
cfg.machine_names, so adding or renaming a machine is a config edit.
"""

import time

from power_monitor import cost

HOUR_SECONDS = 3600
# Overridable per install via [general] hourly_retention_days in config.toml.
DEFAULT_HOURLY_RETENTION_DAYS = 730  # 2 years of hourly history


def hour_start(ts) -> int:
    """Epoch second of the UTC-aligned hour containing ts."""
    return int(ts) - int(ts) % HOUR_SECONDS


def rollup_complete_hours(
    conn, machines, now_ts: float | None = None,
    retention_days: int = DEFAULT_HOURLY_RETENTION_DAYS,
) -> int:
    """Summarize every complete hour not yet in hourly_summary. Returns rows written.

    Only hours that have fully elapsed (hour_end <= now_ts) are rolled up, so the
    in-progress hour is never written as if it were a whole one. Scanning starts at
    the hour after each machine's newest summary (or its oldest raw sample on a
    first-ever run, which backfills all retained history), so a call is cheap even
    with 30 days of raw samples in the table. Hours with no usable samples simply
    get no row.
    """
    now_ts = now_ts if now_ts is not None else time.time()
    end_hour = hour_start(now_ts)  # hours strictly before this one are complete
    written = 0

    for machine in machines:
        row = conn.execute(
            "SELECT MAX(hour_ts) AS last_hour FROM hourly_summary WHERE machine = ?",
            (machine,),
        ).fetchone()
        if row["last_hour"] is not None:
            start_hour = int(row["last_hour"]) + HOUR_SECONDS
        else:
            first = conn.execute(
                "SELECT MIN(ts) AS first_ts FROM samples WHERE machine = ?", (machine,)
            ).fetchone()
            if first["first_ts"] is None:
                continue
            start_hour = hour_start(first["first_ts"])
        if start_hour >= end_hour:
            continue

        buckets = {}
        rows = conn.execute(
            """SELECT ts, cpu_watts, gpu_watts FROM samples
               WHERE machine = ? AND ts >= ? AND ts < ? ORDER BY ts ASC""",
            (machine, start_hour, end_hour),
        )
        for r in rows:
            if r["cpu_watts"] is None and r["gpu_watts"] is None:
                continue
            watts = (r["cpu_watts"] or 0) + (r["gpu_watts"] or 0)
            buckets.setdefault(hour_start(r["ts"]), []).append((r["ts"], watts))

        for hour_ts, points in sorted(buckets.items()):
            watts = [w for _, w in points]
            conn.execute(
                """INSERT OR REPLACE INTO hourly_summary
                   (machine, hour_ts, avg_watts, peak_watts, kwh, sample_count)
                   VALUES (?,?,?,?,?,?)""",
                (
                    machine, hour_ts,
                    sum(watts) / len(watts), max(watts),
                    cost.energy_kwh(points), len(watts),
                ),
            )
            written += 1

    conn.commit()
    prune_old_hourly(conn, retention_days=retention_days, now_ts=now_ts)
    return written


def prune_old_hourly(
    conn, retention_days: int = DEFAULT_HOURLY_RETENTION_DAYS,
    now_ts: float | None = None,
) -> None:
    now_ts = now_ts if now_ts is not None else time.time()
    cutoff = now_ts - retention_days * 86400
    conn.execute("DELETE FROM hourly_summary WHERE hour_ts < ?", (cutoff,))
    conn.commit()


def summarize_range(conn, machines, start_ts, end_ts) -> dict:
    """Per-machine kWh, peak watts and average draw over [start_ts, end_ts).

    avg_watts is kWh-weighted: total energy divided by the hours actually covered
    by hourly data, so collector downtime doesn't drag the average toward zero.
    Machines with no hourly data in the window report 0.0 kWh and None avg/peak.
    """
    summary = {
        m: {"kwh": 0.0, "avg_watts": None, "peak_watts": None, "hours": 0}
        for m in machines
    }
    rows = conn.execute(
        """SELECT machine, SUM(kwh) AS kwh, MAX(peak_watts) AS peak,
                  COUNT(*) AS hours
           FROM hourly_summary WHERE hour_ts >= ? AND hour_ts < ?
           GROUP BY machine""",
        (int(start_ts), int(end_ts)),
    ).fetchall()
    for r in rows:
        if r["machine"] not in summary:
            continue
        hours = r["hours"]
        summary[r["machine"]] = {
            "kwh": r["kwh"] or 0.0,
            "avg_watts": (r["kwh"] or 0.0) * 1000.0 / hours if hours else None,
            "peak_watts": r["peak"],
            "hours": hours,
        }
    return summary


def month_start_ts(year: int, month: int) -> float:
    """Local-time epoch seconds of midnight on the first of that month."""
    return time.mktime((year, month, 1, 0, 0, 0, 0, 1, -1))


def next_month(year: int, month: int) -> tuple:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_key(ts) -> tuple:
    tm = time.localtime(ts)
    return (tm.tm_year, tm.tm_mon)


def monthly_kwh(conn, machines) -> dict:
    """{(year, month): {machine: kwh}} over all hourly history, local calendar."""
    months = {}
    rows = conn.execute(
        "SELECT machine, hour_ts, kwh FROM hourly_summary ORDER BY hour_ts ASC"
    ).fetchall()
    for r in rows:
        bucket = months.setdefault(month_key(r["hour_ts"]), {m: 0.0 for m in machines})
        if r["machine"] in bucket:
            bucket[r["machine"]] += r["kwh"] or 0.0
    return months


def complete_months(conn) -> list:
    """Calendar months fully spanned by hourly coverage, oldest first.

    A month counts only if hourly data exists from its first day 00:00 local
    through its final hour, so a partial first or current month is excluded.
    """
    row = conn.execute(
        "SELECT MIN(hour_ts) AS first_hour, MAX(hour_ts) AS last_hour FROM hourly_summary"
    ).fetchone()
    if row["first_hour"] is None:
        return []
    covered_from = row["first_hour"]
    covered_to = row["last_hour"] + HOUR_SECONDS

    months = []
    year, month = month_key(covered_from)
    while True:
        start = month_start_ts(year, month)
        end = month_start_ts(*next_month(year, month))
        if end > covered_to:
            break
        if start >= covered_from:
            months.append((year, month))
        year, month = next_month(year, month)
    return months
