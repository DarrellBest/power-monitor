import sqlite3
import time
from pathlib import Path

# Overridable per install via [general] raw_retention_days in config.toml.
DEFAULT_RAW_RETENTION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    machine TEXT NOT NULL,
    cpu_watts REAL,
    gpu_watts REAL,
    cpu_util REAL,
    gpu_util REAL,
    top_gpu_process TEXT,
    top_cpu_process TEXT,
    disk_read_bps REAL,
    disk_write_bps REAL,
    net_recv_bps REAL,
    net_sent_bps REAL,
    ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_samples_machine_ts ON samples (machine, ts);

CREATE TABLE IF NOT EXISTS spike_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    machine TEXT NOT NULL,
    draw_watts REAL NOT NULL,
    delta_watts REAL NOT NULL,
    top_gpu_process TEXT,
    top_cpu_process TEXT,
    disk_read_bps REAL,
    disk_write_bps REAL,
    net_recv_bps REAL,
    net_sent_bps REAL,
    alerted INTEGER NOT NULL DEFAULT 0,
    alerted_at REAL
);
CREATE INDEX IF NOT EXISTS idx_spike_alerted ON spike_events (alerted);

CREATE TABLE IF NOT EXISTS hourly_summary (
    machine TEXT NOT NULL,
    hour_ts INTEGER NOT NULL,
    avg_watts REAL,
    peak_watts REAL,
    kwh REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (machine, hour_ts)
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_sample(conn, machine, sample: dict, ts: float | None = None) -> int:
    ts = ts if ts is not None else time.time()
    cur = conn.execute(
        """INSERT INTO samples
           (ts, machine, cpu_watts, gpu_watts, cpu_util, gpu_util,
            top_gpu_process, top_cpu_process,
            disk_read_bps, disk_write_bps, net_recv_bps, net_sent_bps, ok)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ts, machine,
            sample.get("cpu_watts"), sample.get("gpu_watts"),
            sample.get("cpu_util"), sample.get("gpu_util"),
            sample.get("top_gpu_process"), sample.get("top_cpu_process"),
            sample.get("disk_read_bps"), sample.get("disk_write_bps"),
            sample.get("net_recv_bps"), sample.get("net_sent_bps"),
            1 if sample.get("ok", True) else 0,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_sample_near(conn, machine, target_ts, tolerance: float = 30.0):
    return conn.execute(
        """SELECT * FROM samples
           WHERE machine = ? AND ok = 1 AND ts BETWEEN ? AND ?
           ORDER BY ABS(ts - ?) ASC LIMIT 1""",
        (machine, target_ts - tolerance, target_ts + tolerance, target_ts),
    ).fetchone()


def get_recent_samples(conn, machine, since_ts):
    return conn.execute(
        "SELECT * FROM samples WHERE machine = ? AND ts >= ? ORDER BY ts ASC",
        (machine, since_ts),
    ).fetchall()


def prune_old_samples(conn, retention_days: int = DEFAULT_RAW_RETENTION_DAYS) -> None:
    cutoff = time.time() - retention_days * 86400
    conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM spike_events WHERE ts < ?", (cutoff,))
    conn.commit()


def insert_spike_event(
    conn, machine, ts, draw_watts, delta_watts,
    top_gpu_process=None, top_cpu_process=None,
    disk_read_bps=None, disk_write_bps=None,
    net_recv_bps=None, net_sent_bps=None,
) -> int:
    cur = conn.execute(
        """INSERT INTO spike_events
           (ts, machine, draw_watts, delta_watts, top_gpu_process, top_cpu_process,
            disk_read_bps, disk_write_bps, net_recv_bps, net_sent_bps, alerted)
           VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
        (
            ts, machine, draw_watts, delta_watts, top_gpu_process, top_cpu_process,
            disk_read_bps, disk_write_bps, net_recv_bps, net_sent_bps,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_unalerted_spikes(conn):
    return conn.execute(
        "SELECT * FROM spike_events WHERE alerted = 0 ORDER BY ts ASC"
    ).fetchall()


def mark_spike_alerted(conn, event_id, alerted_at: float | None = None) -> None:
    alerted_at = alerted_at if alerted_at is not None else time.time()
    conn.execute(
        "UPDATE spike_events SET alerted = 1, alerted_at = ? WHERE id = ?",
        (alerted_at, event_id),
    )
    conn.commit()


def last_spike_ts(conn, machine):
    row = conn.execute(
        "SELECT MAX(ts) as last FROM spike_events WHERE machine = ?",
        (machine,),
    ).fetchone()
    return row["last"]


def last_any_spike_ts(conn):
    row = conn.execute("SELECT MAX(ts) as last FROM spike_events").fetchone()
    return row["last"]
