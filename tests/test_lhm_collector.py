import requests
from power_monitor.collectors import lhm

URL = "http://10.0.0.5:8085/data.json"

REAL_SHAPED_TREE = {
    "Text": "Sensor",
    "Children": [
        {
            "Text": "DESKTOP-PC",
            "Children": [
                {
                    "Text": "Intel Core i9",
                    "Children": [
                        {
                            "Text": "Powers",
                            "Children": [
                                {"Text": "CPU Package", "Value": "130.6 W", "Children": []},
                                {"Text": "CPU Cores", "Value": "118.0 W", "Children": []},
                            ],
                        }
                    ],
                },
                {
                    "Text": "NVIDIA GeForce RTX 4090",
                    "Children": [
                        {
                            "Text": "Powers",
                            "Children": [
                                {"Text": "GPU Package", "Value": "215.3 W", "Children": []},
                            ],
                        }
                    ],
                },
            ],
        }
    ],
}

TREE_WITHOUT_GPU_POWER = {
    "Text": "Sensor",
    "Children": [
        {
            "Text": "DESKTOP-PC",
            "Children": [
                {
                    "Text": "Intel Core i9",
                    "Children": [
                        {
                            "Text": "Powers",
                            "Children": [
                                {"Text": "CPU Package", "Value": "130.6 W", "Children": []},
                            ],
                        }
                    ],
                },
                {"Text": "NVIDIA GeForce RTX 4090", "Children": []},
            ],
        }
    ],
}


def test_parse_lhm_power_finds_cpu_and_gpu():
    result = lhm.parse_lhm_power(REAL_SHAPED_TREE)
    assert result["cpu_watts"] == 130.6
    assert result["gpu_watts"] == 215.3


def test_parse_lhm_power_gpu_missing_is_none():
    result = lhm.parse_lhm_power(TREE_WITHOUT_GPU_POWER)
    assert result["cpu_watts"] == 130.6
    assert result["gpu_watts"] is None


def test_get_lhm_stats_success(monkeypatch):
    monkeypatch.setattr(lhm, "fetch_lhm_tree", lambda url, timeout=5: REAL_SHAPED_TREE)
    result = lhm.get_lhm_stats(URL)
    assert result["ok"] is True
    assert result["cpu_watts"] == 130.6


def test_get_lhm_stats_passes_the_configured_url_and_timeout(monkeypatch):
    seen = {}

    def fake_fetch(url, timeout=5):
        seen["url"] = url
        seen["timeout"] = timeout
        return REAL_SHAPED_TREE

    monkeypatch.setattr(lhm, "fetch_lhm_tree", fake_fetch)
    lhm.get_lhm_stats(URL, timeout=2)
    assert seen == {"url": URL, "timeout": 2}


def test_get_lhm_stats_handles_network_failure(monkeypatch):
    def raise_error(url, timeout=5):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(lhm, "fetch_lhm_tree", raise_error)
    result = lhm.get_lhm_stats(URL)
    assert result == {"cpu_watts": None, "gpu_watts": None, "ok": False}
