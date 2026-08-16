"""The report/alert entrypoints, driven by a synthetic config with Discord stubbed."""

import time

import pytest

from power_monitor import config, db, discord_notify, graph
from scripts import alert_check, weekly_report

CONFIG_TEXT = """
[general]
db_path = "power.db"
reports_dir = "reports"
rate_per_kwh = 0.50
spike_threshold_watts = 300.0

[[machines]]
name = "server"
label = "My Server"
collector = "local"

[[machines]]
name = "desktop"
label = "Gaming PC"
collector = "lhm"
url = "http://10.0.0.5:8085/data.json"
"""


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG_TEXT)
    return config.load_config(path)


@pytest.fixture
def sent(monkeypatch):
    """Captures Discord posts and skips the PNG render."""
    posts = []
    monkeypatch.setattr(
        discord_notify, "send_message",
        lambda content, image_path=None, env_path=None: posts.append(
            {"content": content, "image_path": image_path, "env_path": env_path}
        ),
    )
    monkeypatch.setattr(
        graph, "render_power_graph",
        lambda series, spikes, out_path, title, **kwargs: posts.append(
            {"series": series, "title": title, "kwargs": kwargs}
        ) or out_path,
    )
    return posts


def seed_samples(cfg, watts_by_machine, count=10):
    conn = db.get_connection(cfg.db_path)
    now = time.time()
    for machine, watts in watts_by_machine.items():
        for i in range(count):
            db.insert_sample(
                conn, machine, {"cpu_watts": watts, "ok": True}, ts=now - i * 10
            )
    return conn


def test_weekly_report_uses_config_labels_machines_and_rate(cfg, sent):
    seed_samples(cfg, {"server": 100.0, "desktop": 200.0})
    weekly_report.main(cfg)

    message = sent[-1]["content"]
    assert "My Server" in message and "Gaming PC" in message
    assert "$0.500/kWh" in message
    assert "- server:" not in message  # raw machine keys never reach the message
    assert sent[-1]["env_path"] == cfg.discord_env_path
    # graph series and legend labels follow the configured machines
    rendered = sent[0]
    assert list(rendered["series"]) == ["server", "desktop"]
    assert rendered["kwargs"]["labels"]["desktop"] == "Gaming PC"


def test_weekly_report_reports_no_spikes_generically(cfg, sent):
    seed_samples(cfg, {"server": 100.0, "desktop": 200.0})
    weekly_report.main(cfg)
    assert "No spikes detected on any monitored machine" in sent[-1]["content"]


def test_weekly_report_labels_a_total_spike(cfg, sent):
    conn = seed_samples(cfg, {"server": 100.0, "desktop": 200.0})
    db.insert_spike_event(conn, "total", ts=time.time(), draw_watts=400.0, delta_watts=100.0)
    weekly_report.main(cfg)
    assert "All machines: 400W total" in sent[-1]["content"]


def test_alert_check_posts_per_machine_lines_and_marks_alerted(cfg, sent):
    conn = seed_samples(cfg, {"server": 100.0, "desktop": 200.0})
    now = time.time()
    db.insert_spike_event(conn, "total", ts=now, draw_watts=400.0, delta_watts=100.0)

    alert_check.main(cfg)

    message = sent[-1]["content"]
    assert "300W threshold" in message
    assert "  My Server: 100W" in message
    assert "  Gaming PC: 200W" in message
    assert db.get_unalerted_spikes(db.get_connection(cfg.db_path)) == []


def test_alert_check_is_a_noop_without_events(cfg, sent):
    seed_samples(cfg, {"server": 100.0})
    alert_check.main(cfg)
    assert sent == []
