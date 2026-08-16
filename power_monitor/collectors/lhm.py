"""LibreHardwareMonitor collector: pulls CPU/GPU watts from its web server.

Point `url` at the machine's LibreHardwareMonitor "Remote Web Server" JSON
endpoint (usually `http://<host>:8085/data.json`).
"""

import requests

DEFAULT_TIMEOUT_SECONDS = 5


def fetch_lhm_tree(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _walk(node, path=""):
    text = node.get("Text", "")
    full_path = f"{path}/{text}" if path else text
    val = node.get("Value", "")
    if val and val not in ("", "Value"):
        yield full_path, val
    for child in node.get("Children", []):
        yield from _walk(child, full_path)


def _parse_watts(value_str):
    try:
        return float(value_str.split()[0])
    except (ValueError, IndexError):
        return None


def parse_lhm_power(tree: dict) -> dict:
    cpu_watts = None
    gpu_watts = None
    for path, val in _walk(tree):
        lower = path.lower()
        if "/powers/" not in lower:
            continue
        if "cpu package" in lower and cpu_watts is None:
            cpu_watts = _parse_watts(val)
        elif ("nvidia" in lower or "geforce" in lower or "radeon" in lower) and gpu_watts is None:
            gpu_watts = _parse_watts(val)
    return {"cpu_watts": cpu_watts, "gpu_watts": gpu_watts}


def get_lhm_stats(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    try:
        tree = fetch_lhm_tree(url, timeout)
    except (requests.RequestException, ValueError):
        return {"cpu_watts": None, "gpu_watts": None, "ok": False}
    parsed = parse_lhm_power(tree)
    parsed["ok"] = parsed["cpu_watts"] is not None
    return parsed
