"""Shared synthetic fixtures. Nothing here points at a real machine."""

import pytest

from power_monitor import config

SYNTHETIC_CONFIG = """
[general]
rate_per_kwh = 0.149
spike_threshold_watts = 1450.0
spike_cooldown_seconds = 900

[[machines]]
name = "server"
label = "My Server"
collector = "local"

[[machines]]
name = "desktop"
label = "Gaming PC"
collector = "lhm"
url = "http://10.0.0.5:8085/data.json"

[[machines]]
name = "gpu-box"
label = "GPU Box"
collector = "ssh_nvidia"
host = "gpu-box.example"
user = "someone"
ssh_key = "keys/gpu-box"
"""


@pytest.fixture
def cfg(tmp_path):
    """A three-machine config, one of each collector type, in a tmp directory."""
    path = tmp_path / "config.toml"
    path.write_text(SYNTHETIC_CONFIG)
    return config.load_config(path)
