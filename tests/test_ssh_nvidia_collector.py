import subprocess

import pytest

from power_monitor.collectors import ssh_nvidia

HOST, USER, KEY = "gpu-box.example", "someone", "/tmp/keys/gpu-box"


def test_parse_gpu_csv():
    result = ssh_nvidia.parse_gpu_csv("18.58, 2\n")
    assert result["gpu_watts"] == pytest.approx(18.58)
    assert result["gpu_util"] == pytest.approx(2.0)


def test_ssh_args_carry_host_user_and_key():
    args = ssh_nvidia.ssh_args(HOST, USER, KEY, machine="gpu-box")
    assert args[0] == "ssh"
    assert args[-1] == f"{USER}@{HOST}"
    assert KEY in args
    assert "BatchMode=yes" in args


def test_control_path_is_per_machine():
    assert ssh_nvidia.control_path("gpu-box") != ssh_nvidia.control_path("other-box")
    assert "gpu-box" in ssh_nvidia.control_path("gpu-box")


def test_get_ssh_nvidia_stats_success(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="18.58, 2\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ssh_nvidia.get_ssh_nvidia_stats(HOST, USER, KEY, machine="gpu-box")
    assert result["ok"] is True
    assert result["gpu_watts"] == pytest.approx(18.58)
    assert f"{USER}@{HOST}" in seen["cmd"]
    assert "nvidia-smi" in seen["cmd"][-1]


def test_get_ssh_nvidia_stats_handles_ssh_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ssh_nvidia.get_ssh_nvidia_stats(HOST, USER, KEY)
    assert result == {"gpu_watts": None, "gpu_util": None, "ok": False}
