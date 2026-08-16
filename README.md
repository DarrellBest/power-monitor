# power-monitor

Estimates power draw (CPU + GPU watts, not true wall watts) across any number
of machines, samples them all every few seconds into a local SQLite database,
and posts Discord alerts when their combined draw spikes plus a weekly
cost/usage report.

Everything site-specific — machines, hosts, keys, paths, your electricity rate
and the thresholds — lives in a single gitignored `config.toml`. No hostnames
or paths are baked into the code.

## Collectors

Each machine in the config picks one collector:

| `collector` | What it reads | How |
|---|---|---|
| `local` | CPU watts, GPU watts/util, CPU util, top processes, disk/net I/O | the machine power-monitor runs on: kernel powercap/RAPL energy counter plus `nvidia-smi` if installed |
| `lhm` | CPU package watts, GPU watts | HTTP GET against a Windows box running [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) with its "Remote Web Server" enabled |
| `ssh_nvidia` | GPU watts/util | runs `nvidia-smi` on a remote Linux box over SSH (key auth, multiplexed connection) |

Machines with no readable sensor simply record a not-ok sample; one machine
being down never blocks the others.

## Requirements

- Python 3.11+ (`tomllib`, `X | None` type hints).
- Python packages: `psutil`, `requests`, `matplotlib`, plus `pytest` for the
  test suite:
  ```
  pip install psutil requests matplotlib pytest
  ```
- For a `local` machine: a readable powercap zone
  (`/sys/class/powercap/intel-rapl:0/energy_uj`) for CPU watts, and
  `nvidia-smi` on `PATH` for GPU watts. Either can be absent — that half of
  the sample is just `NULL`.
- For an `lhm` machine: LibreHardwareMonitor running on it with the remote web
  server enabled, reachable at `http://<host>:8085/data.json`.
- For an `ssh_nvidia` machine: passwordless SSH key access (`BatchMode=yes`)
  and `nvidia-smi` on the remote `PATH`.
- A Discord bot token and channel ID for alert/report delivery.

## Setup

1. Clone the repo anywhere. Paths in the config resolve relative to the config
   file, so no particular location is required.

2. Create your config:
   ```
   cp config.example.toml config.toml
   ```
   `config.toml` is gitignored. Edit it: one `[[machines]]` block per machine,
   plus the `[general]` tunables. Every option is documented inline in
   `config.example.toml`. The loader looks for the config at
   `$POWER_MONITOR_CONFIG` first, then `<repo>/config.toml`.

   A machine's `name` is the key stored in every database row — pick it once
   and leave it alone, since renaming it orphans that machine's history. The
   `label` is the display name in Discord messages and graphs, and can change
   freely.

3. For each `ssh_nvidia` machine, generate a keypair and authorize it on the
   remote host:
   ```
   ssh-keygen -t ed25519 -f keys/<machine> -N ""
   ssh-copy-id -i keys/<machine>.pub <user>@<host>
   ```
   `keys/` is gitignored. Point the machine's `ssh_key` at the private key.

4. Create `.env.discord` at the repo root (gitignored, `KEY=value` per line,
   `#` comments allowed):
   ```
   DISCORD_BOT_TOKEN=<your bot token>
   DISCORD_CHANNEL_ID=<channel id to post into>
   ```
   The bot posts through the Discord REST API, so its token needs permission
   to send messages and attach files in that channel. Set
   `discord_env_path` in `[general]` to keep this file elsewhere.

5. Install the systemd user units:
   ```
   mkdir -p ~/.config/systemd/user
   cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now power-collector.service
   systemctl --user enable --now power-alert-watchdog.timer
   systemctl --user enable --now power-weekly-report.timer
   ```
   The units assume the repo is at `~/power-monitor` and that `python3` on
   `PATH` has the dependencies installed. If either differs, edit
   `WorkingDirectory=` and `ExecStart=` in your copies under
   `~/.config/systemd/user/` (e.g. point `ExecStart` at a virtualenv or conda
   interpreter) and `systemctl --user daemon-reload`.

   To keep the units running after you log out:
   ```
   loginctl enable-linger $USER
   ```

| Unit | Type | Schedule |
|---|---|---|
| `power-collector.service` | long-running | samples every `sample_interval_seconds`, always on (`Restart=on-failure`) |
| `power-alert-watchdog.timer` | oneshot | every minute — posts unalerted spikes to Discord |
| `power-weekly-report.timer` | oneshot | Sundays at 20:00 local — posts the weekly report |

Change the report schedule with `OnCalendar=` in
`systemd/power-weekly-report.timer`, then `systemctl --user daemon-reload &&
systemctl --user restart power-weekly-report.timer`.

## Config knobs

All in `[general]`, all optional (defaults shown):

| Key | Default | Meaning |
|---|---|---|
| `db_path` | `power.db` | SQLite database, created on first run |
| `reports_dir` | `reports` | where generated PNGs land |
| `discord_env_path` | `.env.discord` | Discord credentials file |
| `rate_per_kwh` | `0.149` | your billed electricity rate in $/kWh — the default is only an example, set your own |
| `spike_threshold_watts` | `1450.0` | combined estimated draw across all machines that fires an alert |
| `spike_cooldown_seconds` | `900` | minimum gap between recorded spike events |
| `sample_interval_seconds` | `10` | polling interval; rollup runs hourly and pruning daily, derived from it |
| `raw_retention_days` | `30` | how long per-sample rows are kept |
| `hourly_retention_days` | `730` | how long hourly summaries are kept (2 years) |
| `report_retention_days` | `30` | how long PNGs in `reports_dir` are kept |

Relative paths resolve against the config file's directory; `~` is expanded.

## Running tests

```
pytest
```

The suite is fully offline — no network, no SSH, no real config: collectors,
Discord and the graph renderer are stubbed, and every test builds its own
synthetic config in a temp directory.

## Data

`power.db` (gitignored, created on first run) has three tables:

- `samples` — one row per machine per sampling round (watts, utilization, top
  processes, disk/net I/O).
- `spike_events` — one row per detected combined-power spike, recorded under
  the reserved machine name `total`.
- `hourly_summary` — one row per machine per completed hour (avg/peak watts,
  kWh, sample count), retained far longer than raw samples so the weekly
  report's trend and monthly sections survive raw-sample pruning.

`reports/` (gitignored) holds the PNG graphs attached to each weekly report and
spike alert.

## Layout

```
power_monitor/
  config.py      config file loading + validation (the only source of site values)
  sampler.py     one sampling round; maps collector type -> collector adapter
  collectors/    local.py, lhm.py, ssh_nvidia.py
  db.py          SQLite schema and queries
  spike.py       combined-total spike detection with cooldown
  rollup.py      raw samples -> hourly summaries, monthly/weekly aggregation
  cost.py        trapezoidal kWh integration and cost
  graph.py       matplotlib renderer, palette assignment
  reports.py     report file retention
  discord_notify.py  Discord Bot REST delivery
scripts/         run_sampler_loop.py, alert_check.py, weekly_report.py
systemd/         user units for the collector, watchdog timer and report timer
```
