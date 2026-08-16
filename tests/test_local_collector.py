import subprocess
import pytest
from power_monitor.collectors import local


def test_compute_watts_from_energy_normal():
    watts = local.compute_watts_from_energy(e1_uj=1_000_000, t1=0.0, e2_uj=6_000_000, t2=1.0, max_energy_range_uj=100_000_000_000)
    assert watts == pytest.approx(5.0)


def test_compute_watts_from_energy_handles_wraparound():
    max_range = 1_000_000
    watts = local.compute_watts_from_energy(e1_uj=900_000, t1=0.0, e2_uj=100_000, t2=1.0, max_energy_range_uj=max_range)
    # counter wrapped: (max_range - 900_000) + 100_000 = 200_000 uj over 1s = 0.2W
    assert watts == pytest.approx(0.2)


def test_compute_watts_from_energy_zero_or_negative_dt():
    assert local.compute_watts_from_energy(1, 5.0, 2, 5.0, 1_000_000) is None
    assert local.compute_watts_from_energy(1, 5.0, 2, 4.0, 1_000_000) is None


def test_read_rapl_energy_uj_parses_file(tmp_path):
    p = tmp_path / "energy_uj"
    p.write_text("123456\n")
    assert local.read_rapl_energy_uj(path=str(p)) == 123456


def test_get_gpu_stats_parses_nvidia_smi(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--query-gpu=power.draw,utilization.gpu" in cmd[1]:
            return subprocess.CompletedProcess(cmd, 0, stdout="37.37, 2\n", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0, stdout="steamwebhelper, 16\nllama-server, 88822\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    stats = local.get_gpu_stats()
    assert stats["gpu_watts"] == pytest.approx(37.37)
    assert stats["gpu_util"] == pytest.approx(2.0)
    assert stats["top_gpu_process"] == "llama-server"


def test_get_gpu_stats_handles_missing_nvidia_smi(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    stats = local.get_gpu_stats()
    assert stats == {"gpu_watts": None, "gpu_util": None, "top_gpu_process": None}


def test_get_io_stats_computes_rate():
    prev = {"disk_read": 1000, "disk_write": 500, "net_recv": 2000, "net_sent": 1000}
    now = {"disk_read": 1100, "disk_write": 600, "net_recv": 2200, "net_sent": 1050}
    result = local.get_io_stats(prev, 0.0, now, 2.0)
    assert result["disk_read_bps"] == pytest.approx(50.0)
    assert result["disk_write_bps"] == pytest.approx(50.0)
    assert result["net_recv_bps"] == pytest.approx(100.0)
    assert result["net_sent_bps"] == pytest.approx(25.0)


def test_rapl_paths_derive_from_the_zone_directory():
    assert local.rapl_energy_path("/sys/class/powercap/intel-rapl:1").endswith(
        "intel-rapl:1/energy_uj"
    )
    assert local.rapl_max_range_path("/sys/class/powercap/intel-rapl:1").endswith(
        "intel-rapl:1/max_energy_range_uj"
    )
    assert local.rapl_energy_path().startswith(local.DEFAULT_RAPL_PATH)


def test_read_rapl_max_range_uj_parses_file(tmp_path):
    p = tmp_path / "max_energy_range_uj"
    p.write_text("262143328850\n")
    assert local.read_rapl_max_range_uj(str(p)) == 262143328850
