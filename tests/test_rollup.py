import time

import pytest

from power_monitor import cost, db, rollup

HOUR = 3600
H0 = 3600 * 400000  # an hour-aligned epoch second
MACHINES = ("server", "desktop", "gpu-box")


@pytest.fixture
def conn(tmp_path):
    return db.get_connection(str(tmp_path / "test.db"))


def fill_hour(conn, machine, hour_ts, cpu=None, gpu=None, count=360, step=10.0):
    """Insert `count` samples at `step` seconds apart starting at hour_ts."""
    for i in range(count):
        db.insert_sample(
            conn, machine, {"cpu_watts": cpu, "gpu_watts": gpu, "ok": True},
            ts=hour_ts + i * step,
        )


def hourly_rows(conn):
    return conn.execute(
        "SELECT * FROM hourly_summary ORDER BY machine, hour_ts"
    ).fetchall()


def test_hour_start_floors_to_utc_hour():
    assert rollup.hour_start(H0) == H0
    assert rollup.hour_start(H0 + 1) == H0
    assert rollup.hour_start(H0 + 3599.9) == H0
    assert rollup.hour_start(H0 + HOUR) == H0 + HOUR


def test_hourly_summary_table_exists(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(hourly_summary)")}
    assert cols == {
        "machine", "hour_ts", "avg_watts", "peak_watts", "kwh", "sample_count",
    }


def test_aggregates_one_complete_hour(conn):
    fill_hour(conn, "server", H0, cpu=100.0, gpu=50.0)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 2 * HOUR)

    rows = hourly_rows(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["machine"] == "server"
    assert r["hour_ts"] == H0
    assert r["sample_count"] == 360
    assert r["avg_watts"] == pytest.approx(150.0)
    assert r["peak_watts"] == pytest.approx(150.0)
    # 359 intervals of 10s at 150W
    assert r["kwh"] == pytest.approx(359 * 10 * 150 / 3600.0 / 1000.0)


def test_peak_and_avg_track_varying_draw(conn):
    db.insert_sample(conn, "server", {"cpu_watts": 100.0, "ok": True}, ts=H0)
    db.insert_sample(conn, "server", {"cpu_watts": 200.0, "ok": True}, ts=H0 + 10)
    db.insert_sample(conn, "server", {"cpu_watts": 300.0, "gpu_watts": 100.0, "ok": True},
                     ts=H0 + 20)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + HOUR)

    r = hourly_rows(conn)[0]
    assert r["sample_count"] == 3
    assert r["peak_watts"] == pytest.approx(400.0)
    assert r["avg_watts"] == pytest.approx((100 + 200 + 400) / 3.0)
    assert r["kwh"] == pytest.approx(
        cost.energy_kwh([(H0, 100.0), (H0 + 10, 200.0), (H0 + 20, 400.0)])
    )


def test_null_watt_rows_are_skipped_but_partial_nulls_count(conn):
    # gpu-only row counts, both-NULL row does not
    db.insert_sample(conn, "gpu-box", {"gpu_watts": 60.0, "ok": True}, ts=H0)
    db.insert_sample(conn, "gpu-box", {"ok": False}, ts=H0 + 10)
    db.insert_sample(conn, "gpu-box", {"gpu_watts": 40.0, "ok": True}, ts=H0 + 20)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + HOUR)

    r = hourly_rows(conn)[0]
    assert r["sample_count"] == 2
    assert r["avg_watts"] == pytest.approx(50.0)
    assert r["peak_watts"] == pytest.approx(60.0)


def test_hour_with_no_usable_samples_has_no_row(conn):
    fill_hour(conn, "server", H0, cpu=100.0)
    fill_hour(conn, "server", H0 + HOUR, cpu=None, gpu=None, count=5)
    fill_hour(conn, "server", H0 + 2 * HOUR, cpu=100.0)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 3 * HOUR)

    assert [r["hour_ts"] for r in hourly_rows(conn)] == [H0, H0 + 2 * HOUR]


def test_incomplete_current_hour_is_excluded(conn):
    fill_hour(conn, "server", H0, cpu=100.0)
    fill_hour(conn, "server", H0 + HOUR, cpu=100.0, count=10)
    # now is 100s into the second hour: that hour is not complete yet
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + HOUR + 100)

    assert [r["hour_ts"] for r in hourly_rows(conn)] == [H0]


def test_hour_is_rolled_up_once_it_completes(conn):
    fill_hour(conn, "server", H0, cpu=100.0)
    fill_hour(conn, "server", H0 + HOUR, cpu=200.0)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + HOUR + 100)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 2 * HOUR + 5)

    rows = hourly_rows(conn)
    assert [r["hour_ts"] for r in rows] == [H0, H0 + HOUR]
    assert rows[1]["avg_watts"] == pytest.approx(200.0)


def test_is_idempotent(conn):
    fill_hour(conn, "server", H0, cpu=100.0)
    fill_hour(conn, "desktop", H0, cpu=50.0)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 2 * HOUR)
    before = [dict(r) for r in hourly_rows(conn)]
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 2 * HOUR)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 2 * HOUR)
    assert [dict(r) for r in hourly_rows(conn)] == before


def test_backfills_all_machines_from_scratch(conn):
    for h in range(5):
        fill_hour(conn, "server", H0 + h * HOUR, cpu=100.0)
        fill_hour(conn, "desktop", H0 + h * HOUR, cpu=200.0)
        fill_hour(conn, "gpu-box", H0 + h * HOUR, gpu=300.0)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 5 * HOUR)

    rows = hourly_rows(conn)
    assert len(rows) == 15
    for machine in MACHINES:
        hours = [r["hour_ts"] for r in rows if r["machine"] == machine]
        assert hours == [H0 + h * HOUR for h in range(5)]


def test_incremental_run_only_scans_new_hours(conn):
    fill_hour(conn, "server", H0, cpu=100.0)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + HOUR)
    # a late-arriving sample in an already-rolled-up hour is not re-read
    db.insert_sample(conn, "server", {"cpu_watts": 9999.0, "ok": True}, ts=H0 + 5)
    fill_hour(conn, "server", H0 + HOUR, cpu=200.0)
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 2 * HOUR)

    rows = hourly_rows(conn)
    assert rows[0]["peak_watts"] == pytest.approx(100.0)
    assert rows[1]["avg_watts"] == pytest.approx(200.0)


def test_returns_number_of_hours_written(conn):
    fill_hour(conn, "server", H0, cpu=100.0)
    fill_hour(conn, "server", H0 + HOUR, cpu=100.0)
    assert rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 2 * HOUR) == 2
    assert rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0 + 2 * HOUR) == 0


def test_no_samples_at_all_is_a_noop(conn):
    assert rollup.rollup_complete_hours(conn, MACHINES, now_ts=H0) == 0
    assert hourly_rows(conn) == []


def test_defaults_now_ts_to_wall_clock(conn):
    now = time.time()
    hour = rollup.hour_start(now) - HOUR
    fill_hour(conn, "server", hour, cpu=100.0, count=10)
    rollup.rollup_complete_hours(conn, MACHINES)
    assert [r["hour_ts"] for r in hourly_rows(conn)] == [hour]


def test_prune_old_hourly_drops_rows_past_retention(conn):
    now = time.time()
    old = rollup.hour_start(now - (rollup.DEFAULT_HOURLY_RETENTION_DAYS + 5) * 86400)
    recent = rollup.hour_start(now - 86400)
    for h in (old, recent):
        conn.execute(
            "INSERT INTO hourly_summary VALUES (?,?,?,?,?,?)",
            ("server", h, 100.0, 100.0, 0.1, 360),
        )
    conn.commit()
    rollup.prune_old_hourly(conn)
    assert [r["hour_ts"] for r in hourly_rows(conn)] == [recent]


def test_rollup_prunes_hourly_as_it_goes(conn):
    now = time.time()
    old = rollup.hour_start(now - (rollup.DEFAULT_HOURLY_RETENTION_DAYS + 5) * 86400)
    conn.execute(
        "INSERT INTO hourly_summary VALUES (?,?,?,?,?,?)",
        ("server", old, 100.0, 100.0, 0.1, 360),
    )
    conn.commit()
    rollup.rollup_complete_hours(conn, MACHINES, now_ts=now)
    assert hourly_rows(conn) == []


def test_retention_is_two_years(conn):
    assert rollup.DEFAULT_HOURLY_RETENTION_DAYS == 730


# --- report helpers -------------------------------------------------------

def test_summarize_range_returns_kwh_avg_peak_per_machine(conn):
    rows = [
        ("server", H0, 100.0, 500.0, 0.1, 360),
        ("server", H0 + HOUR, 200.0, 300.0, 0.2, 360),
        ("desktop", H0, 50.0, 60.0, 0.05, 360),
        ("server", H0 + 5 * HOUR, 999.0, 9999.0, 9.9, 360),  # outside range
    ]
    conn.executemany("INSERT INTO hourly_summary VALUES (?,?,?,?,?,?)", rows)
    conn.commit()

    summary = rollup.summarize_range(conn, MACHINES, H0, H0 + 2 * HOUR)
    assert summary["server"]["kwh"] == pytest.approx(0.3)
    assert summary["server"]["peak_watts"] == pytest.approx(500.0)
    assert summary["server"]["hours"] == 2
    # kWh-weighted average draw over the hours actually covered
    assert summary["server"]["avg_watts"] == pytest.approx(150.0)
    assert summary["desktop"]["kwh"] == pytest.approx(0.05)
    assert summary["gpu-box"]["kwh"] == 0.0
    assert summary["gpu-box"]["avg_watts"] is None
    assert summary["gpu-box"]["peak_watts"] is None


def test_month_bounds_uses_local_calendar():
    start = rollup.month_start_ts(2026, 8)
    tm = time.localtime(start)
    assert (tm.tm_year, tm.tm_mon, tm.tm_mday, tm.tm_hour) == (2026, 8, 1, 0)
    end = rollup.month_start_ts(2026, 9)
    assert rollup.next_month(2026, 8) == (2026, 9)
    assert rollup.next_month(2026, 12) == (2027, 1)
    assert end > start


def test_monthly_kwh_groups_by_local_month(conn):
    aug = rollup.month_start_ts(2026, 8)
    sep = rollup.month_start_ts(2026, 9)
    conn.executemany(
        "INSERT INTO hourly_summary VALUES (?,?,?,?,?,?)",
        [
            ("server", int(aug), 100.0, 100.0, 0.1, 360),
            ("server", int(aug) + HOUR, 100.0, 100.0, 0.2, 360),
            ("server", int(sep), 100.0, 100.0, 0.4, 360),
            ("desktop", int(sep), 100.0, 100.0, 1.0, 360),
        ],
    )
    conn.commit()
    monthly = rollup.monthly_kwh(conn, MACHINES)
    assert monthly[(2026, 8)]["server"] == pytest.approx(0.3)
    assert monthly[(2026, 9)]["server"] == pytest.approx(0.4)
    assert monthly[(2026, 9)]["desktop"] == pytest.approx(1.0)


def test_complete_months_requires_full_coverage(conn):
    aug = int(rollup.month_start_ts(2026, 8))
    sep = int(rollup.month_start_ts(2026, 9))
    oct_ = int(rollup.month_start_ts(2026, 10))
    # coverage starts mid-August and ends mid-October: only September is complete
    conn.executemany(
        "INSERT INTO hourly_summary VALUES (?,?,?,?,?,?)",
        [
            ("server", aug + 10 * 86400, 100.0, 100.0, 0.1, 360),
            ("server", sep, 100.0, 100.0, 0.2, 360),
            ("server", oct_ - HOUR, 100.0, 100.0, 0.2, 360),
            ("server", oct_ + 10 * 86400, 100.0, 100.0, 0.3, 360),
        ],
    )
    conn.commit()
    assert rollup.complete_months(conn) == [(2026, 9)]


def test_complete_months_empty_when_no_hourly_data(conn):
    assert rollup.complete_months(conn) == []
