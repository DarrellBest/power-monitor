"""Collector for the machine the monitor itself runs on.

CPU watts come from the kernel's powercap/RAPL energy counter (Intel and recent
AMD chips expose one); GPU watts from `nvidia-smi` if it is installed. Either
half degrades to None on its own rather than failing the sample.
"""

import os
import subprocess

import psutil

# The powercap zone directory holding energy_uj/max_energy_range_uj. Override
# per machine with `rapl_path` in config.toml on hosts where package power is a
# different zone (e.g. intel-rapl:1).
DEFAULT_RAPL_PATH = "/sys/class/powercap/intel-rapl:0"


def rapl_energy_path(rapl_path: str = DEFAULT_RAPL_PATH) -> str:
    return os.path.join(rapl_path, "energy_uj")


def rapl_max_range_path(rapl_path: str = DEFAULT_RAPL_PATH) -> str:
    return os.path.join(rapl_path, "max_energy_range_uj")


def read_rapl_energy_uj(path: str | None = None) -> int:
    with open(path or rapl_energy_path()) as f:
        return int(f.read().strip())


def read_rapl_max_range_uj(path: str | None = None) -> int:
    with open(path or rapl_max_range_path()) as f:
        return int(f.read().strip())


def compute_watts_from_energy(e1_uj, t1, e2_uj, t2, max_energy_range_uj):
    dt = t2 - t1
    if dt <= 0:
        return None
    de = e2_uj - e1_uj
    if de < 0:
        de += max_energy_range_uj
    return (de / 1_000_000) / dt


def get_gpu_stats() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        power_str, util_str = [p.strip() for p in out.split(",")]
        power_w, util_pct = float(power_str), float(util_str)
    except (subprocess.SubprocessError, ValueError, FileNotFoundError, OSError):
        return {"gpu_watts": None, "gpu_util": None, "top_gpu_process": None}

    top_process = None
    try:
        proc_out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        if proc_out:
            rows = [
                (name.strip(), int(mem.strip()))
                for name, mem in (line.split(",") for line in proc_out.splitlines())
            ]
            rows.sort(key=lambda r: r[1], reverse=True)
            top_process = rows[0][0]
    except (subprocess.SubprocessError, ValueError, FileNotFoundError, OSError):
        pass

    return {"gpu_watts": power_w, "gpu_util": util_pct, "top_gpu_process": top_process}


def get_cpu_stats() -> dict:
    cpu_util = psutil.cpu_percent(interval=None)
    top_process = None
    try:
        # First call after service start returns 0.0 for every process
        # (psutil needs a prior reading per-process to compute a delta);
        # it self-corrects on the next 10s sample.
        procs = sorted(
            psutil.process_iter(["name", "cpu_percent"]),
            key=lambda p: p.info["cpu_percent"] or 0,
            reverse=True,
        )
        if procs:
            top_process = procs[0].info["name"]
    except Exception:
        pass
    return {"cpu_util": cpu_util, "top_cpu_process": top_process}


def read_io_counters() -> dict:
    disk = psutil.disk_io_counters()
    net = psutil.net_io_counters()
    return {
        "disk_read": disk.read_bytes if disk else 0,
        "disk_write": disk.write_bytes if disk else 0,
        "net_recv": net.bytes_recv,
        "net_sent": net.bytes_sent,
    }


def get_io_stats(prev_io, prev_ts, now_io, now_ts) -> dict:
    dt = now_ts - prev_ts
    if dt <= 0:
        return {"disk_read_bps": None, "disk_write_bps": None,
                "net_recv_bps": None, "net_sent_bps": None}
    return {
        "disk_read_bps": (now_io["disk_read"] - prev_io["disk_read"]) / dt,
        "disk_write_bps": (now_io["disk_write"] - prev_io["disk_write"]) / dt,
        "net_recv_bps": (now_io["net_recv"] - prev_io["net_recv"]) / dt,
        "net_sent_bps": (now_io["net_sent"] - prev_io["net_sent"]) / dt,
    }
