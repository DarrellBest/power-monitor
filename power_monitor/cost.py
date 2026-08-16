# Fallback electricity rate in $/kWh, used when no rate is passed in. Set your
# own billed rate (off your latest statement) as rate_per_kwh in config.toml.
DEFAULT_RATE_PER_KWH = 0.149

# Samples arrive every ~10s; a longer stretch means the collector was down,
# so that interval contributes no energy rather than an invented average.
MAX_GAP_SECONDS = 30.0


def energy_kwh(points, max_gap_seconds: float = MAX_GAP_SECONDS) -> float:
    """Trapezoidal integration of (ts, watts) points, ascending by ts, to kWh.

    Intervals longer than max_gap_seconds are treated as collector downtime and
    contribute zero energy. Fewer than two points is 0.0.
    """
    total_watt_seconds = 0.0
    for (t0, w0), (t1, w1) in zip(points, points[1:]):
        dt = t1 - t0
        if dt <= 0 or dt > max_gap_seconds:
            continue
        total_watt_seconds += (w0 + w1) / 2.0 * dt
    return total_watt_seconds / 3600.0 / 1000.0


def cost_usd(kwh: float, rate: float = DEFAULT_RATE_PER_KWH) -> float:
    return kwh * rate
