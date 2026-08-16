import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from power_monitor import config, db, rollup, sampler  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("power-collector")


def loops_per(seconds: float, interval_seconds: float) -> int:
    """How many sampling loops fit in `seconds`, at least one."""
    return max(1, round(seconds / interval_seconds))


def main(cfg=None):
    cfg = cfg or config.load_config()
    conn = db.get_connection(cfg.db_path)
    state = sampler.SamplerState()
    interval = cfg.sample_interval_seconds
    rollup_every = loops_per(3600, interval)   # hourly
    prune_every = loops_per(86400, interval)   # daily
    log.info(
        "sampling %s every %ss", ", ".join(cfg.machine_names), interval
    )

    loop_count = 0
    while True:
        start = time.time()
        try:
            sampler.run_one_round(conn, state, cfg)
        except Exception:
            log.exception("sampler round failed")
        loop_count += 1
        # Rollup runs before prune so complete hours are summarized before the raw
        # samples behind them can be deleted.
        if loop_count % rollup_every == 0:
            try:
                rollup.rollup_complete_hours(
                    conn, cfg.machine_names, retention_days=cfg.hourly_retention_days
                )
            except Exception:
                log.exception("hourly rollup failed")
        if loop_count % prune_every == 0:
            db.prune_old_samples(conn, retention_days=cfg.raw_retention_days)
        elapsed = time.time() - start
        time.sleep(max(0, interval - elapsed))


if __name__ == "__main__":
    main()
