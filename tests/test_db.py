import time
import pytest
from power_monitor import db


@pytest.fixture
def conn(tmp_path):
    return db.get_connection(str(tmp_path / "test.db"))


def test_insert_and_fetch_sample(conn):
    ts = 1000.0
    db.insert_sample(conn, "server", {"cpu_watts": 42.5, "gpu_watts": 10.0, "ok": True}, ts=ts)
    row = db.get_sample_near(conn, "server", ts, tolerance=1.0)
    assert row is not None
    assert row["machine"] == "server"
    assert row["cpu_watts"] == 42.5
    assert row["gpu_watts"] == 10.0


def test_get_sample_near_respects_tolerance(conn):
    db.insert_sample(conn, "server", {"cpu_watts": 1.0, "ok": True}, ts=1000.0)
    assert db.get_sample_near(conn, "server", 1500.0, tolerance=30.0) is None
    assert db.get_sample_near(conn, "server", 1015.0, tolerance=30.0) is not None


def test_get_recent_samples_orders_by_ts(conn):
    db.insert_sample(conn, "server", {"cpu_watts": 1.0, "ok": True}, ts=1000.0)
    db.insert_sample(conn, "server", {"cpu_watts": 2.0, "ok": True}, ts=1010.0)
    db.insert_sample(conn, "desktop", {"cpu_watts": 99.0, "ok": True}, ts=1005.0)
    rows = db.get_recent_samples(conn, "server", since_ts=0)
    assert [r["cpu_watts"] for r in rows] == [1.0, 2.0]


def test_prune_old_samples(conn):
    now = time.time()
    db.insert_sample(conn, "server", {"cpu_watts": 1.0, "ok": True}, ts=now - 40 * 86400)
    db.insert_sample(conn, "server", {"cpu_watts": 2.0, "ok": True}, ts=now)
    db.prune_old_samples(conn, retention_days=30)
    rows = db.get_recent_samples(conn, "server", since_ts=0)
    assert len(rows) == 1
    assert rows[0]["cpu_watts"] == 2.0


def test_spike_event_lifecycle(conn):
    event_id = db.insert_spike_event(
        conn, "server", ts=2000.0, draw_watts=500.0, delta_watts=200.0,
        top_gpu_process="game.exe",
    )
    unalerted = db.get_unalerted_spikes(conn)
    assert len(unalerted) == 1
    assert unalerted[0]["id"] == event_id
    assert unalerted[0]["top_gpu_process"] == "game.exe"

    db.mark_spike_alerted(conn, event_id, alerted_at=2001.0)
    assert db.get_unalerted_spikes(conn) == []
    assert db.last_spike_ts(conn, "server") == 2000.0


def test_last_spike_ts_none_when_no_spikes(conn):
    assert db.last_spike_ts(conn, "server") is None


def test_last_any_spike_ts_ignores_machine(conn):
    assert db.last_any_spike_ts(conn) is None
    db.insert_spike_event(conn, "server", ts=2000.0, draw_watts=500.0, delta_watts=200.0)
    db.insert_spike_event(conn, "total", ts=2500.0, draw_watts=1500.0, delta_watts=50.0)
    assert db.last_any_spike_ts(conn) == 2500.0
