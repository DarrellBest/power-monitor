"""Remote GPU collector: runs `nvidia-smi` over SSH on another machine.

Needs passwordless key auth (`BatchMode=yes`); the connection is multiplexed
over a per-machine control socket so a 10s sampling interval doesn't pay for a
fresh SSH handshake every round.
"""

import subprocess

DEFAULT_TIMEOUT_SECONDS = 10
CONTROL_PATH_TEMPLATE = "/tmp/power-monitor-{machine}-ssh-%r@%h:%p"
NVIDIA_SMI_QUERY = (
    "nvidia-smi --query-gpu=power.draw,utilization.gpu "
    "--format=csv,noheader,nounits"
)


def control_path(machine: str) -> str:
    return CONTROL_PATH_TEMPLATE.format(machine=machine)


def ssh_args(host: str, user: str, key_path: str, machine: str = "host") -> list:
    return [
        "ssh",
        "-i", key_path,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={control_path(machine)}",
        "-o", "ControlPersist=5m",
        f"{user}@{host}",
    ]


def parse_gpu_csv(csv_line: str) -> dict:
    power_str, util_str = [p.strip() for p in csv_line.strip().split(",")]
    return {"gpu_watts": float(power_str), "gpu_util": float(util_str)}


def get_ssh_nvidia_stats(
    host: str, user: str, key_path: str, machine: str = "host",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    try:
        out = subprocess.run(
            ssh_args(host, user, key_path, machine) + [NVIDIA_SMI_QUERY],
            capture_output=True, text=True, timeout=timeout, check=True,
        ).stdout
        parsed = parse_gpu_csv(out)
        parsed["ok"] = True
        return parsed
    except (subprocess.SubprocessError, ValueError, FileNotFoundError, OSError):
        return {"gpu_watts": None, "gpu_util": None, "ok": False}
