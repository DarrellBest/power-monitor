import os
import time

# Overridable per install via [general] report_retention_days in config.toml.
DEFAULT_REPORT_RETENTION_DAYS = 30


def prune_old_reports(
    reports_dir: str, retention_days: int = DEFAULT_REPORT_RETENTION_DAYS
) -> None:
    """Delete files in reports_dir older than retention_days, mirroring db.prune_old_samples."""
    cutoff = time.time() - retention_days * 86400
    os.makedirs(reports_dir, exist_ok=True)
    for name in os.listdir(reports_dir):
        path = os.path.join(reports_dir, name)
        if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
            os.remove(path)
