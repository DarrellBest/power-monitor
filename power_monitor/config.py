"""Site configuration: everything machine-, path- and rate-specific lives here.

The repo ships `config.example.toml`; each install copies it to `config.toml`
(gitignored) and edits it. Nothing in the code hardcodes a host, IP, user or
absolute path — `load_config()` is the only source of those.

Resolution order for the config file:

1. an explicit `path` argument,
2. `$POWER_MONITOR_CONFIG`,
3. `<repo root>/config.toml`.

Relative paths inside the file resolve against the config file's own directory,
so a repo cloned anywhere works without edits to the paths.
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_ENV_VAR = "POWER_MONITOR_CONFIG"
DEFAULT_CONFIG_NAME = "config.toml"

# The machine key spike events are recorded under: a combined all-machine event
# rather than any single configured machine.
TOTAL_MACHINE = "total"
TOTAL_LABEL = "All machines"

# collector type -> (required machine keys, optional machine keys). The sampler
# holds the matching callables (power_monitor.sampler.COLLECTOR_SAMPLERS); the
# two key sets are kept in step by a test.
COLLECTOR_FIELDS = {
    "local": ((), ("rapl_path",)),
    "lhm": (("url",), ("timeout_seconds",)),
    "ssh_nvidia": (("host", "user", "ssh_key"), ("timeout_seconds",)),
}
COLLECTOR_TYPES = tuple(COLLECTOR_FIELDS)

MACHINE_COMMON_KEYS = ("name", "label", "collector")

GENERAL_DEFAULTS = {
    "db_path": "power.db",
    "reports_dir": "reports",
    "discord_env_path": ".env.discord",
    "rate_per_kwh": 0.149,
    "spike_threshold_watts": 1450.0,
    "spike_cooldown_seconds": 900,
    "raw_retention_days": 30,
    "hourly_retention_days": 730,
    "report_retention_days": 30,
    "sample_interval_seconds": 10,
}
# Paths are resolved against the config file's directory.
PATH_KEYS = ("db_path", "reports_dir", "discord_env_path")
POSITIVE_NUMBER_KEYS = (
    "rate_per_kwh", "spike_threshold_watts", "spike_cooldown_seconds",
    "raw_retention_days", "hourly_retention_days", "report_retention_days",
    "sample_interval_seconds",
)


class ConfigError(ValueError):
    """Raised for a missing, unreadable or invalid config file."""


@dataclass(frozen=True)
class Machine:
    """One monitored machine. `settings` holds the collector-specific keys."""

    name: str
    label: str
    collector: str
    settings: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    path: Path
    db_path: str
    reports_dir: str
    discord_env_path: str
    rate_per_kwh: float
    spike_threshold_watts: float
    spike_cooldown_seconds: float
    raw_retention_days: int
    hourly_retention_days: int
    report_retention_days: int
    sample_interval_seconds: float
    machines: tuple

    @property
    def machine_names(self) -> tuple:
        return tuple(m.name for m in self.machines)

    @property
    def labels(self) -> dict:
        """{machine name: display label}, plus the combined-total pseudo-machine."""
        labels = {m.name: m.label for m in self.machines}
        labels.setdefault(TOTAL_MACHINE, TOTAL_LABEL)
        return labels

    def label_for(self, machine: str) -> str:
        """Display label for a machine key, falling back to the key itself.

        Rows for machines that were removed from the config still live in the
        database, so an unknown key is shown as-is rather than being an error.
        """
        return self.labels.get(machine, machine)


def config_path(path=None) -> Path:
    """The config file that would be loaded, without reading it."""
    if path:
        return Path(path).expanduser()
    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser()
    return REPO_ROOT / DEFAULT_CONFIG_NAME


def load_config(path=None) -> Config:
    resolved = config_path(path)
    if not resolved.is_file():
        raise ConfigError(
            f"No config file at {resolved}. Copy config.example.toml to "
            f"{REPO_ROOT / DEFAULT_CONFIG_NAME} and edit it, or point "
            f"${CONFIG_ENV_VAR} at your own copy."
        )
    try:
        with open(resolved, "rb") as f:
            raw = tomllib.load(f)
        return parse_config(raw, resolved)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{resolved} is not valid TOML: {exc}") from exc


def parse_config(raw: dict, path) -> Config:
    """Validate a parsed TOML mapping into a Config. Raises ConfigError."""
    path = Path(path)
    base_dir = path.parent

    unknown_sections = set(raw) - {"general", "machines"}
    if unknown_sections:
        raise ConfigError(
            f"{path}: unknown top-level section(s) {sorted(unknown_sections)}; "
            "expected [general] and [[machines]]"
        )

    general = raw.get("general", {})
    if not isinstance(general, dict):
        raise ConfigError(f"{path}: [general] must be a table")
    unknown_keys = set(general) - set(GENERAL_DEFAULTS)
    if unknown_keys:
        raise ConfigError(
            f"{path}: unknown key(s) in [general]: {sorted(unknown_keys)}; "
            f"known keys are {sorted(GENERAL_DEFAULTS)}"
        )

    values = dict(GENERAL_DEFAULTS)
    values.update(general)
    for key in POSITIVE_NUMBER_KEYS:
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path}: [general] {key} must be a number, got {value!r}")
        if value <= 0:
            raise ConfigError(f"{path}: [general] {key} must be greater than 0, got {value!r}")
    for key in PATH_KEYS:
        value = values[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{path}: [general] {key} must be a non-empty string")
        values[key] = resolve_path(value, base_dir)

    machines = parse_machines(raw.get("machines", []), path, base_dir)

    return Config(path=path, machines=machines, **values)


def parse_machines(entries, path, base_dir) -> tuple:
    if not isinstance(entries, list) or not entries:
        raise ConfigError(
            f"{path}: at least one [[machines]] entry is required "
            "(see config.example.toml)"
        )

    machines = []
    seen = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: [[machines]] #{index} must be a table")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{path}: [[machines]] #{index} needs a non-empty name")
        if name in seen:
            raise ConfigError(
                f"{path}: duplicate machine name {name!r}; names are the key "
                "stored in the database and must be unique"
            )
        seen.add(name)
        if name == TOTAL_MACHINE:
            raise ConfigError(
                f"{path}: {TOTAL_MACHINE!r} is reserved for combined-total spike "
                "events and cannot be used as a machine name"
            )

        collector = entry.get("collector")
        if collector not in COLLECTOR_FIELDS:
            raise ConfigError(
                f"{path}: machine {name!r} has unknown collector {collector!r}; "
                f"known collectors are {list(COLLECTOR_TYPES)}"
            )

        label = entry.get("label", name)
        if not isinstance(label, str) or not label.strip():
            raise ConfigError(f"{path}: machine {name!r} has an empty label")

        required, optional = COLLECTOR_FIELDS[collector]
        allowed = set(MACHINE_COMMON_KEYS) | set(required) | set(optional)
        unknown = set(entry) - allowed
        if unknown:
            raise ConfigError(
                f"{path}: machine {name!r} ({collector}) has unknown key(s) "
                f"{sorted(unknown)}; allowed keys are {sorted(allowed)}"
            )
        missing = [key for key in required if not str(entry.get(key, "")).strip()]
        if missing:
            raise ConfigError(
                f"{path}: machine {name!r} uses collector {collector!r} and is "
                f"missing required key(s) {missing}"
            )

        settings = {}
        for key in (*required, *optional):
            if key not in entry:
                continue
            value = entry[key]
            settings[key] = resolve_path(value, base_dir) if _is_path_key(key) else value

        machines.append(Machine(name=name, label=label, collector=collector, settings=settings))

    return tuple(machines)


def resolve_path(value: str, base_dir) -> str:
    """Absolute path for a config value, relative paths against the config's dir."""
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    return str((Path(base_dir) / expanded).resolve())


def _is_path_key(key: str) -> bool:
    return key in ("ssh_key", "rapl_path")
