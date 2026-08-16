"""Discord Bot REST delivery.

Credentials live in a gitignored `.env.discord` (`DISCORD_BOT_TOKEN` and
`DISCORD_CHANNEL_ID`, one `KEY=value` per line) next to the repo by default;
`[general] discord_env_path` in config.toml can point somewhere else.
"""

import os
from pathlib import Path

import requests

DEFAULT_ENV_PATH = str(Path(__file__).resolve().parent.parent / ".env.discord")
API_BASE = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/power-monitor, 1.0)"


def load_discord_env(env_path: str = DEFAULT_ENV_PATH) -> dict:
    env = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key] = value
    return env


def send_message(
    content: str, image_path: str | None = None,
    env_path: str = DEFAULT_ENV_PATH,
) -> dict:
    env = load_discord_env(env_path)
    headers = {
        "Authorization": f"Bot {env['DISCORD_BOT_TOKEN']}",
        "User-Agent": USER_AGENT,
    }
    url = f"{API_BASE}/channels/{env['DISCORD_CHANNEL_ID']}/messages"

    if image_path:
        with open(image_path, "rb") as f:
            files = {"file": (os.path.basename(image_path), f, "image/png")}
            resp = requests.post(
                url, headers=headers, data={"content": content}, files=files, timeout=15
            )
    else:
        resp = requests.post(url, headers=headers, json={"content": content}, timeout=15)

    resp.raise_for_status()
    return resp.json()
