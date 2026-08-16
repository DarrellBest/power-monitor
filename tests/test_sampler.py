import pytest

from power_monitor import config, db, sampler
from power_monitor.collectors import lhm, local, ssh_nvidia


@pytest.fixture
def conn(tmp_path):
    return db.get_connection(str(tmp_path / "test.db"))


@pytest.fixture
def stub_collectors(monkeypatch):
    """Every collector stubbed; individual tests override what they care about."""
    monkeypatch.setattr(local, "read_rapl_energy_uj", lambda path=None: 1_000_000)
    monkeypatch.setattr(local, "read_rapl_max_range_uj", lambda path=None: 100_000_000_000)
    monkeypatch.setattr(local, "get_gpu_stats", lambda: {"gpu_watts": 30.0, "gpu_util": 5.0, "top_gpu_process": None})
    monkeypatch.setattr(local, "get_cpu_stats", lambda: {"cpu_util": 10.0, "top_cpu_process": "python"})
    monkeypatch.setattr(local, "read_io_counters", lambda: {"disk_read": 0, "disk_write": 0, "net_recv": 0, "net_sent": 0})
    monkeypatch.setattr(lhm, "get_lhm_stats", lambda url, timeout=5: {"cpu_watts": 90.0, "gpu_watts": None, "ok": True})
    monkeypatch.setattr(
        ssh_nvidia, "get_ssh_nvidia_stats",
        lambda host, user, key_path, machine="host", timeout=10: {
            "gpu_watts": 18.5, "gpu_util": 2.0, "ok": True,
        },
    )
    return monkeypatch


def test_collector_registry_matches_the_configs_known_collectors():
    assert set(sampler.COLLECTOR_SAMPLERS) == set(config.COLLECTOR_TYPES)


def test_run_one_round_writes_every_configured_machine(conn, cfg, stub_collectors):
    state = sampler.SamplerState()
    samples = sampler.run_one_round(conn, state, cfg)

    assert list(samples) == ["server", "desktop", "gpu-box"]
    assert samples["server"]["gpu_watts"] == 30.0
    assert samples["desktop"]["cpu_watts"] == 90.0
    assert samples["gpu-box"]["gpu_watts"] == 18.5

    for machine in cfg.machine_names:
        rows = db.get_recent_samples(conn, machine, since_ts=0)
        assert len(rows) == 1


def test_run_one_round_marks_failed_machine_not_ok(conn, cfg, stub_collectors):
    stub_collectors.setattr(
        lhm, "get_lhm_stats",
        lambda url, timeout=5: {"cpu_watts": None, "gpu_watts": None, "ok": False},
    )
    samples = sampler.run_one_round(conn, sampler.SamplerState(), cfg)
    assert samples["desktop"]["ok"] is False


def test_collectors_receive_their_configured_settings(conn, cfg, stub_collectors):
    seen = {}
    stub_collectors.setattr(
        lhm, "get_lhm_stats",
        lambda url, timeout=5: seen.update(url=url) or {"cpu_watts": 1.0, "ok": True},
    )
    stub_collectors.setattr(
        ssh_nvidia, "get_ssh_nvidia_stats",
        lambda host, user, key_path, machine="host", timeout=10: seen.update(
            host=host, user=user, key_path=key_path, machine=machine
        ) or {"gpu_watts": 1.0, "ok": True},
    )
    sampler.run_one_round(conn, sampler.SamplerState(), cfg)

    assert seen["url"] == "http://10.0.0.5:8085/data.json"
    assert seen["host"] == "gpu-box.example"
    assert seen["user"] == "someone"
    assert seen["key_path"].endswith("/keys/gpu-box")
    assert seen["machine"] == "gpu-box"


def test_one_machine_config_works(conn, tmp_path, stub_collectors):
    path = tmp_path / "one.toml"
    path.write_text('[[machines]]\nname = "only"\ncollector = "local"\n')
    one = config.load_config(path)
    samples = sampler.run_one_round(conn, sampler.SamplerState(), one)
    assert list(samples) == ["only"]


def test_local_rapl_state_is_kept_per_machine(conn, tmp_path, stub_collectors):
    path = tmp_path / "two.toml"
    path.write_text(
        '[[machines]]\nname = "a"\ncollector = "local"\n'
        '[[machines]]\nname = "b"\ncollector = "local"\n'
    )
    two = config.load_config(path)
    state = sampler.SamplerState()
    assert state.for_machine("a") is not state.for_machine("b")

    # First round has no previous energy reading, so no CPU watts for either.
    first = sampler.run_one_round(conn, state, two)
    assert first["a"]["cpu_watts"] is None and first["b"]["cpu_watts"] is None
    second = sampler.run_one_round(conn, state, two)
    assert second["a"]["cpu_watts"] is not None
    assert second["b"]["cpu_watts"] is not None


def test_spike_uses_the_configured_threshold(conn, cfg, stub_collectors):
    stub_collectors.setattr(
        lhm, "get_lhm_stats",
        lambda url, timeout=5: {"cpu_watts": 2000.0, "gpu_watts": None, "ok": True},
    )
    sampler.run_one_round(conn, sampler.SamplerState(), cfg)
    events = db.get_unalerted_spikes(conn)
    assert len(events) == 1
    assert events[0]["machine"] == "total"


def test_blank_sample_has_every_column():
    sample = sampler.blank_sample(cpu_watts=1.0, ok=True)
    assert set(sample) == set(sampler.SAMPLE_FIELDS) | {"ok"}
    assert sample["cpu_watts"] == 1.0
    assert sample["net_sent_bps"] is None
