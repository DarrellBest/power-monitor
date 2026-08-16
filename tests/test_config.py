import pytest

from power_monitor import config

MINIMAL = """
[[machines]]
name = "server"
label = "My Server"
collector = "local"
"""

FULL = """
[general]
db_path = "power.db"
reports_dir = "reports"
rate_per_kwh = 0.20
spike_threshold_watts = 900.0
spike_cooldown_seconds = 60
raw_retention_days = 7
hourly_retention_days = 365
report_retention_days = 14
sample_interval_seconds = 5

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


def write_config(tmp_path, text, name="config.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_loads_machines_in_file_order(tmp_path):
    cfg = config.load_config(write_config(tmp_path, FULL))
    assert cfg.machine_names == ("server", "desktop", "gpu-box")
    assert [m.collector for m in cfg.machines] == ["local", "lhm", "ssh_nvidia"]
    assert cfg.machines[1].settings["url"] == "http://10.0.0.5:8085/data.json"
    assert cfg.machines[2].settings["host"] == "gpu-box.example"


def test_labels_include_total_pseudo_machine(tmp_path):
    cfg = config.load_config(write_config(tmp_path, FULL))
    assert cfg.labels["desktop"] == "Gaming PC"
    assert cfg.label_for("total") == "All machines"
    assert cfg.label_for("retired-box") == "retired-box"


def test_general_values_are_read(tmp_path):
    cfg = config.load_config(write_config(tmp_path, FULL))
    assert cfg.rate_per_kwh == 0.20
    assert cfg.spike_threshold_watts == 900.0
    assert cfg.spike_cooldown_seconds == 60
    assert cfg.raw_retention_days == 7
    assert cfg.hourly_retention_days == 365
    assert cfg.report_retention_days == 14
    assert cfg.sample_interval_seconds == 5


def test_general_section_is_optional_and_defaults_apply(tmp_path):
    cfg = config.load_config(write_config(tmp_path, MINIMAL))
    assert cfg.rate_per_kwh == config.GENERAL_DEFAULTS["rate_per_kwh"]
    assert cfg.spike_threshold_watts == config.GENERAL_DEFAULTS["spike_threshold_watts"]
    assert cfg.hourly_retention_days == 730
    assert cfg.db_path == str(tmp_path / "power.db")


def test_label_defaults_to_name(tmp_path):
    cfg = config.load_config(write_config(tmp_path, '[[machines]]\nname = "box"\ncollector = "local"\n'))
    assert cfg.machines[0].label == "box"


def test_relative_paths_resolve_against_the_config_directory(tmp_path):
    cfg = config.load_config(write_config(tmp_path, FULL))
    assert cfg.db_path == str(tmp_path / "power.db")
    assert cfg.reports_dir == str(tmp_path / "reports")
    assert cfg.discord_env_path == str(tmp_path / ".env.discord")
    assert cfg.machines[2].settings["ssh_key"] == str(tmp_path / "keys" / "gpu-box")


def test_absolute_paths_are_left_alone(tmp_path):
    cfg = config.load_config(write_config(tmp_path, MINIMAL + '\n[general]\ndb_path = "/var/lib/pm/power.db"\n'))
    assert cfg.db_path == "/var/lib/pm/power.db"


def test_env_var_is_used_when_no_path_given(tmp_path, monkeypatch):
    path = write_config(tmp_path, MINIMAL, name="elsewhere.toml")
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(path))
    assert config.load_config().path == path
    assert config.config_path() == path


def test_explicit_path_wins_over_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(tmp_path / "nope.toml"))
    path = write_config(tmp_path, MINIMAL)
    assert config.load_config(path).path == path


def test_falls_back_to_repo_root_config(tmp_path, monkeypatch):
    monkeypatch.delenv(config.CONFIG_ENV_VAR, raising=False)
    assert config.config_path() == config.REPO_ROOT / "config.toml"


def test_missing_file_names_the_path_and_the_example(tmp_path, monkeypatch):
    monkeypatch.delenv(config.CONFIG_ENV_VAR, raising=False)
    with pytest.raises(config.ConfigError) as exc:
        config.load_config(tmp_path / "absent.toml")
    assert "absent.toml" in str(exc.value)
    assert "config.example.toml" in str(exc.value)


def test_invalid_toml_is_reported_clearly(tmp_path):
    path = write_config(tmp_path, "this is not toml =\n")
    with pytest.raises(config.ConfigError, match="not valid TOML"):
        config.load_config(path)


def test_duplicate_machine_names_are_rejected(tmp_path):
    text = MINIMAL + '\n[[machines]]\nname = "server"\ncollector = "local"\n'
    with pytest.raises(config.ConfigError, match="duplicate machine name"):
        config.load_config(write_config(tmp_path, text))


def test_total_is_a_reserved_machine_name(tmp_path):
    text = '[[machines]]\nname = "total"\ncollector = "local"\n'
    with pytest.raises(config.ConfigError, match="reserved"):
        config.load_config(write_config(tmp_path, text))


def test_unknown_collector_lists_the_known_ones(tmp_path):
    text = '[[machines]]\nname = "box"\ncollector = "smoke-signals"\n'
    with pytest.raises(config.ConfigError) as exc:
        config.load_config(write_config(tmp_path, text))
    assert "smoke-signals" in str(exc.value)
    for known in config.COLLECTOR_TYPES:
        assert known in str(exc.value)


def test_missing_required_collector_field_is_rejected(tmp_path):
    text = '[[machines]]\nname = "desktop"\ncollector = "lhm"\n'
    with pytest.raises(config.ConfigError) as exc:
        config.load_config(write_config(tmp_path, text))
    assert "url" in str(exc.value)


def test_ssh_collector_requires_host_user_and_key(tmp_path):
    text = '[[machines]]\nname = "gpu"\ncollector = "ssh_nvidia"\nhost = "h"\n'
    with pytest.raises(config.ConfigError) as exc:
        config.load_config(write_config(tmp_path, text))
    assert "user" in str(exc.value) and "ssh_key" in str(exc.value)


def test_unknown_machine_key_is_rejected(tmp_path):
    text = '[[machines]]\nname = "box"\ncollector = "local"\nurl = "http://x/"\n'
    with pytest.raises(config.ConfigError, match="unknown key"):
        config.load_config(write_config(tmp_path, text))


def test_unknown_general_key_is_rejected(tmp_path):
    text = MINIMAL + '\n[general]\nrate_per_kwhh = 0.1\n'
    with pytest.raises(config.ConfigError, match="unknown key"):
        config.load_config(write_config(tmp_path, text))


def test_unknown_top_level_section_is_rejected(tmp_path):
    text = MINIMAL + '\n[discord]\ntoken = "x"\n'
    with pytest.raises(config.ConfigError, match="unknown top-level section"):
        config.load_config(write_config(tmp_path, text))


def test_no_machines_is_rejected(tmp_path):
    with pytest.raises(config.ConfigError, match="at least one"):
        config.load_config(write_config(tmp_path, "[general]\nrate_per_kwh = 0.1\n"))


@pytest.mark.parametrize("key", config.POSITIVE_NUMBER_KEYS)
def test_non_positive_numbers_are_rejected(tmp_path, key):
    text = MINIMAL + f'\n[general]\n{key} = 0\n'
    with pytest.raises(config.ConfigError, match="greater than 0"):
        config.load_config(write_config(tmp_path, text))


def test_non_numeric_tunable_is_rejected(tmp_path):
    text = MINIMAL + '\n[general]\nrate_per_kwh = "cheap"\n'
    with pytest.raises(config.ConfigError, match="must be a number"):
        config.load_config(write_config(tmp_path, text))


def test_shipped_example_config_is_valid():
    """config.example.toml is what users copy — it must always parse and validate."""
    cfg = config.load_config(config.REPO_ROOT / "config.example.toml")
    assert {m.collector for m in cfg.machines} == set(config.COLLECTOR_TYPES)
    assert cfg.machine_names == ("server", "desktop", "gpu-box")
    assert cfg.rate_per_kwh == config.GENERAL_DEFAULTS["rate_per_kwh"]
    assert cfg.spike_threshold_watts == config.GENERAL_DEFAULTS["spike_threshold_watts"]
