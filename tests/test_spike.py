import pytest
from power_monitor import db, spike


@pytest.fixture
def conn(tmp_path):
    return db.get_connection(str(tmp_path / "test.db"))


def test_total_watts_sums_available_parts():
    assert spike.total_watts({"cpu_watts": 40.0, "gpu_watts": 10.0}) == 50.0
    assert spike.total_watts({"cpu_watts": None, "gpu_watts": 10.0}) == 10.0
    assert spike.total_watts({"cpu_watts": None, "gpu_watts": None}) is None


def test_fires_when_combined_total_reaches_threshold(conn):
    samples = {
        "server": {"cpu_watts": 120.0, "gpu_watts": 430.0, "ok": True},
        "desktop": {"cpu_watts": 150.0, "gpu_watts": 600.0, "ok": True},
        "gpu-box": {"cpu_watts": None, "gpu_watts": 150.0, "ok": True},
    }
    event_id = spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples)
    assert event_id is not None

    unalerted = db.get_unalerted_spikes(conn)
    assert len(unalerted) == 1
    assert unalerted[0]["machine"] == "total"
    assert unalerted[0]["draw_watts"] == pytest.approx(1450.0)
    assert unalerted[0]["delta_watts"] == pytest.approx(0.0)
    assert unalerted[0]["ts"] == 1000.0


def test_ignores_total_below_threshold(conn):
    samples = {
        "server": {"cpu_watts": 100.0, "gpu_watts": 300.0, "ok": True},
        "desktop": {"cpu_watts": 140.0, "gpu_watts": 500.0, "ok": True},
        "gpu-box": {"cpu_watts": None, "gpu_watts": 100.0, "ok": True},
    }
    assert spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples) is None
    assert db.get_unalerted_spikes(conn) == []


def test_delta_watts_is_amount_over_threshold(conn):
    samples = {
        "server": {"cpu_watts": 200.0, "gpu_watts": 400.0, "ok": True},
        "desktop": {"cpu_watts": 200.0, "gpu_watts": 700.0, "ok": True},
        "gpu-box": {"cpu_watts": None, "gpu_watts": 50.0, "ok": True},
    }
    spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples)
    event = db.get_unalerted_spikes(conn)[0]
    assert event["draw_watts"] == pytest.approx(1550.0)
    assert event["delta_watts"] == pytest.approx(100.0)


def test_offline_and_all_none_machines_contribute_zero(conn):
    samples = {
        # Offline: its watts must not count even though they're populated.
        "server": {"cpu_watts": 500.0, "gpu_watts": 500.0, "ok": False},
        "desktop": {"cpu_watts": 200.0, "gpu_watts": 800.0, "ok": True},
        "gpu-box": {"cpu_watts": None, "gpu_watts": None, "ok": True},
    }
    assert spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples) is None

    samples["desktop"] = {"cpu_watts": 250.0, "gpu_watts": 1200.0, "ok": True}
    assert spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples) is not None
    assert db.get_unalerted_spikes(conn)[0]["draw_watts"] == pytest.approx(1450.0)


def test_none_watts_treated_as_zero_within_a_machine(conn):
    samples = {
        "server": {"cpu_watts": None, "gpu_watts": 700.0, "ok": True},
        "desktop": {"cpu_watts": 750.0, "gpu_watts": None, "ok": True},
        "gpu-box": {"cpu_watts": None, "gpu_watts": None, "ok": True},
    }
    assert spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples) is not None
    assert db.get_unalerted_spikes(conn)[0]["draw_watts"] == pytest.approx(1450.0)


def test_cooldown_suppresses_second_event(conn):
    samples = {
        "server": {"cpu_watts": 150.0, "gpu_watts": 450.0, "ok": True},
        "desktop": {"cpu_watts": 200.0, "gpu_watts": 600.0, "ok": True},
        "gpu-box": {"cpu_watts": None, "gpu_watts": 100.0, "ok": True},
    }
    assert spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples) is not None
    assert spike.check_and_record_total_spike(conn, now_ts=1500.0, samples=samples) is None
    assert len(db.get_unalerted_spikes(conn)) == 1


def test_cooldown_is_machine_agnostic(conn):
    db.insert_spike_event(conn, "server", ts=1000.0, draw_watts=500.0, delta_watts=200.0)
    samples = {
        "server": {"cpu_watts": 150.0, "gpu_watts": 450.0, "ok": True},
        "desktop": {"cpu_watts": 200.0, "gpu_watts": 600.0, "ok": True},
        "gpu-box": {"cpu_watts": None, "gpu_watts": 100.0, "ok": True},
    }
    assert spike.check_and_record_total_spike(conn, now_ts=1300.0, samples=samples) is None


def test_fires_again_after_cooldown_expires(conn):
    samples = {
        "server": {"cpu_watts": 150.0, "gpu_watts": 450.0, "ok": True},
        "desktop": {"cpu_watts": 200.0, "gpu_watts": 600.0, "ok": True},
        "gpu-box": {"cpu_watts": None, "gpu_watts": 100.0, "ok": True},
    }
    assert spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples) is not None
    later = 1000.0 + spike.DEFAULT_COOLDOWN_SECONDS + 1
    assert spike.check_and_record_total_spike(conn, now_ts=later, samples=samples) is not None
    assert len(db.get_unalerted_spikes(conn)) == 2


def test_attribution_fields_come_from_top_contributing_machine(conn):
    samples = {
        "server": {
            "cpu_watts": 100.0, "gpu_watts": 200.0, "ok": True,
            "top_gpu_process": "render", "top_cpu_process": "compile",
            "disk_read_bps": 1.0, "disk_write_bps": 2.0,
            "net_recv_bps": 3.0, "net_sent_bps": 4.0,
        },
        "desktop": {
            "cpu_watts": 250.0, "gpu_watts": 800.0, "ok": True,
            "top_gpu_process": "game.exe", "top_cpu_process": "shader-compiler.exe",
            "disk_read_bps": 11.0, "disk_write_bps": 22.0,
            "net_recv_bps": 33.0, "net_sent_bps": 44.0,
        },
        "gpu-box": {
            "cpu_watts": None, "gpu_watts": 200.0, "ok": True,
            "top_gpu_process": "train.py", "top_cpu_process": None,
            "disk_read_bps": 5.0, "disk_write_bps": 6.0,
            "net_recv_bps": 7.0, "net_sent_bps": 8.0,
        },
    }
    spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples)
    event = db.get_unalerted_spikes(conn)[0]
    assert event["top_gpu_process"] == "game.exe"
    assert event["top_cpu_process"] == "shader-compiler.exe"
    assert event["disk_read_bps"] == 11.0
    assert event["disk_write_bps"] == 22.0
    assert event["net_recv_bps"] == 33.0
    assert event["net_sent_bps"] == 44.0


def test_offline_machine_is_never_the_top_contributor(conn):
    samples = {
        "server": {
            "cpu_watts": 900.0, "gpu_watts": 900.0, "ok": False,
            "top_gpu_process": "stale", "top_cpu_process": "stale",
        },
        "desktop": {
            "cpu_watts": 250.0, "gpu_watts": 1000.0, "ok": True,
            "top_gpu_process": "game.exe", "top_cpu_process": None,
        },
        "gpu-box": {
            "cpu_watts": None, "gpu_watts": 300.0, "ok": True,
            "top_gpu_process": "train.py", "top_cpu_process": None,
        },
    }
    spike.check_and_record_total_spike(conn, now_ts=1000.0, samples=samples)
    event = db.get_unalerted_spikes(conn)[0]
    assert event["top_gpu_process"] == "game.exe"


def test_empty_samples_returns_none(conn):
    assert spike.check_and_record_total_spike(conn, now_ts=1000.0, samples={}) is None


def test_threshold_is_configurable(conn):
    samples = {"server": {"cpu_watts": 300.0, "gpu_watts": 0.0, "ok": True}}
    assert spike.check_and_record_total_spike(conn, 1000.0, samples) is None
    assert spike.check_and_record_total_spike(
        conn, 1000.0, samples, threshold_watts=250.0
    ) is not None
    event = db.get_unalerted_spikes(conn)[0]
    assert event["delta_watts"] == pytest.approx(50.0)


def test_cooldown_is_configurable(conn):
    samples = {"server": {"cpu_watts": 300.0, "gpu_watts": 0.0, "ok": True}}
    kwargs = {"threshold_watts": 250.0, "cooldown_seconds": 60}
    assert spike.check_and_record_total_spike(conn, 1000.0, samples, **kwargs) is not None
    assert spike.check_and_record_total_spike(conn, 1030.0, samples, **kwargs) is None
    assert spike.check_and_record_total_spike(conn, 1061.0, samples, **kwargs) is not None
    assert len(db.get_unalerted_spikes(conn)) == 2


def test_defaults_match_the_shipped_example_config():
    assert spike.DEFAULT_THRESHOLD_WATTS == 1450.0
    assert spike.DEFAULT_COOLDOWN_SECONDS == 900
