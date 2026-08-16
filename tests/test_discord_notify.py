import requests
from power_monitor import discord_notify


def test_load_discord_env_parses_key_value_pairs(tmp_path):
    env_file = tmp_path / ".env.discord"
    env_file.write_text("DISCORD_BOT_TOKEN=abc123\nDISCORD_CHANNEL_ID=999\n")
    env = discord_notify.load_discord_env(str(env_file))
    assert env == {"DISCORD_BOT_TOKEN": "abc123", "DISCORD_CHANNEL_ID": "999"}


def test_load_discord_env_skips_blank_and_comment_lines(tmp_path):
    env_file = tmp_path / ".env.discord"
    env_file.write_text("# comment\n\nDISCORD_BOT_TOKEN=abc123\nDISCORD_CHANNEL_ID=999\n")
    env = discord_notify.load_discord_env(str(env_file))
    assert env == {"DISCORD_BOT_TOKEN": "abc123", "DISCORD_CHANNEL_ID": "999"}


def test_send_message_text_only_uses_json_post(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.discord"
    env_file.write_text("DISCORD_BOT_TOKEN=abc123\nDISCORD_CHANNEL_ID=999\n")

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "111", "content": captured.get("json", {}).get("content")}

    def fake_post(url, headers=None, json=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["data"] = data
        captured["files"] = files
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    result = discord_notify.send_message("hello world", env_path=str(env_file))

    assert result["content"] == "hello world"
    assert captured["url"] == "https://discord.com/api/v10/channels/999/messages"
    assert captured["headers"]["Authorization"] == "Bot abc123"
    assert "DiscordBot" in captured["headers"]["User-Agent"]
    assert captured["json"] == {"content": "hello world"}
    assert captured["files"] is None


def test_send_message_with_image_uses_multipart_post(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.discord"
    env_file.write_text("DISCORD_BOT_TOKEN=abc123\nDISCORD_CHANNEL_ID=999\n")
    image_file = tmp_path / "graph.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\nfakepngdata")

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "112"}

    def fake_post(url, headers=None, json=None, data=None, files=None, timeout=None):
        captured["json"] = json
        captured["data"] = data
        captured["files"] = files
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    discord_notify.send_message("spike report", image_path=str(image_file), env_path=str(env_file))

    assert captured["json"] is None
    assert captured["data"] == {"content": "spike report"}
    assert captured["files"] is not None
    assert captured["files"]["file"][0] == "graph.png"
