"""One sampling round across every configured machine.

Each collector type has an adapter here that turns its raw reading into the one
sample shape the database stores; COLLECTOR_SAMPLERS maps the collector names
config.py validates against those adapters.
"""

import time

from power_monitor import db
from power_monitor import spike as spike_mod
from power_monitor.collectors import lhm, local, ssh_nvidia

SAMPLE_FIELDS = (
    "cpu_watts", "gpu_watts", "cpu_util", "gpu_util",
    "top_gpu_process", "top_cpu_process",
    "disk_read_bps", "disk_write_bps", "net_recv_bps", "net_sent_bps",
)


def blank_sample(**values) -> dict:
    """A sample with every field present, so partial collectors stay uniform."""
    sample = {field: None for field in SAMPLE_FIELDS}
    sample["ok"] = False
    sample.update(values)
    return sample


class MachineState:
    """Per-machine carry-over between rounds (RAPL and I/O counters are deltas)."""

    def __init__(self):
        self.prev_energy_uj = None
        self.prev_energy_ts = None
        self.prev_io = None
        self.prev_io_ts = None


class SamplerState:
    def __init__(self):
        self._by_machine = {}

    def for_machine(self, name: str) -> MachineState:
        return self._by_machine.setdefault(name, MachineState())


def _sample_local(machine, state: MachineState, now_ts: float) -> dict:
    rapl_path = machine.settings.get("rapl_path")
    energy_path = local.rapl_energy_path(rapl_path) if rapl_path else None
    max_range_path = local.rapl_max_range_path(rapl_path) if rapl_path else None

    cpu_watts = None
    try:
        energy_uj = local.read_rapl_energy_uj(energy_path)
        if state.prev_energy_uj is not None:
            max_range = local.read_rapl_max_range_uj(max_range_path)
            cpu_watts = local.compute_watts_from_energy(
                state.prev_energy_uj, state.prev_energy_ts, energy_uj, now_ts, max_range
            )
        state.prev_energy_uj = energy_uj
        state.prev_energy_ts = now_ts
    except OSError:
        pass

    gpu_stats = local.get_gpu_stats()
    cpu_stats = local.get_cpu_stats()

    io_stats = {}
    now_io = local.read_io_counters()
    if state.prev_io is not None:
        io_stats = local.get_io_stats(state.prev_io, state.prev_io_ts, now_io, now_ts)
    state.prev_io = now_io
    state.prev_io_ts = now_ts

    return blank_sample(
        cpu_watts=cpu_watts,
        gpu_watts=gpu_stats["gpu_watts"],
        cpu_util=cpu_stats["cpu_util"],
        gpu_util=gpu_stats["gpu_util"],
        top_gpu_process=gpu_stats["top_gpu_process"],
        top_cpu_process=cpu_stats["top_cpu_process"],
        **io_stats,
        ok=True,
    )


def _sample_lhm(machine, state: MachineState, now_ts: float) -> dict:
    result = lhm.get_lhm_stats(
        machine.settings["url"],
        timeout=machine.settings.get("timeout_seconds", lhm.DEFAULT_TIMEOUT_SECONDS),
    )
    return blank_sample(
        cpu_watts=result.get("cpu_watts"),
        gpu_watts=result.get("gpu_watts"),
        ok=result.get("ok", False),
    )


def _sample_ssh_nvidia(machine, state: MachineState, now_ts: float) -> dict:
    result = ssh_nvidia.get_ssh_nvidia_stats(
        machine.settings["host"],
        machine.settings["user"],
        machine.settings["ssh_key"],
        machine=machine.name,
        timeout=machine.settings.get("timeout_seconds", ssh_nvidia.DEFAULT_TIMEOUT_SECONDS),
    )
    return blank_sample(
        gpu_watts=result.get("gpu_watts"),
        gpu_util=result.get("gpu_util"),
        ok=result.get("ok", False),
    )


COLLECTOR_SAMPLERS = {
    "local": _sample_local,
    "lhm": _sample_lhm,
    "ssh_nvidia": _sample_ssh_nvidia,
}


def run_one_round(conn, state: SamplerState, cfg) -> dict:
    now_ts = time.time()
    samples = {}
    for machine in cfg.machines:
        collect = COLLECTOR_SAMPLERS[machine.collector]
        samples[machine.name] = collect(machine, state.for_machine(machine.name), now_ts)
    for name, sample in samples.items():
        db.insert_sample(conn, name, sample, ts=now_ts)
    spike_mod.check_and_record_total_spike(
        conn, now_ts, samples,
        threshold_watts=cfg.spike_threshold_watts,
        cooldown_seconds=cfg.spike_cooldown_seconds,
    )
    return samples
