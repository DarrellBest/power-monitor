from power_monitor import db

# Overridable per install via [general] spike_threshold_watts /
# spike_cooldown_seconds in config.toml.
DEFAULT_THRESHOLD_WATTS = 1450.0
DEFAULT_COOLDOWN_SECONDS = 900


def total_watts(sample: dict):
    parts = [v for v in (sample.get("cpu_watts"), sample.get("gpu_watts")) if v is not None]
    if not parts:
        return None
    return sum(parts)


def _contribution(sample: dict) -> float:
    """What this machine adds to the combined total: 0 if offline or fully unreadable."""
    if not sample.get("ok", True):
        return 0.0
    watts = total_watts(sample)
    return 0.0 if watts is None else watts


def check_and_record_total_spike(
    conn, now_ts: float, samples: dict,
    threshold_watts: float = DEFAULT_THRESHOLD_WATTS,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
):
    """Fire on the combined estimated draw of every machine in one sampling round."""
    contributions = {machine: _contribution(s) for machine, s in samples.items()}
    total = sum(contributions.values())
    if total < threshold_watts:
        return None

    last_ts = db.last_any_spike_ts(conn)
    if last_ts is not None and (now_ts - last_ts) < cooldown_seconds:
        return None

    # Attribute the event to whichever single machine drew the most this round.
    top_machine = max(contributions, key=contributions.get)
    top_sample = samples[top_machine]

    return db.insert_spike_event(
        conn, "total", now_ts, total, total - threshold_watts,
        top_gpu_process=top_sample.get("top_gpu_process"),
        top_cpu_process=top_sample.get("top_cpu_process"),
        disk_read_bps=top_sample.get("disk_read_bps"),
        disk_write_bps=top_sample.get("disk_write_bps"),
        net_recv_bps=top_sample.get("net_recv_bps"),
        net_sent_bps=top_sample.get("net_sent_bps"),
    )
