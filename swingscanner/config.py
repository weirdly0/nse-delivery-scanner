"""Config loader with environment-variable overrides for secrets."""

from __future__ import annotations
import os
import yaml
from pathlib import Path
from typing import Any


class Config:
    """Dot-access wrapper around the YAML config dict."""

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._data:
            raise AttributeError(f"No config key: {name}")
        v = self._data[name]
        return Config(v) if isinstance(v, dict) else v

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> dict:
        return self._data


def load_config(path: str | Path = "config.yaml") -> Config:
    """
    Load YAML config and apply environment-variable overrides for
    Telegram secrets.  Env vars always win over YAML values.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Copy config.yaml.example to config.yaml and edit it."
        )

    with open(path) as f:
        data = yaml.safe_load(f)

    # Env-var overrides for secrets — never commit tokens to YAML
    env_token = os.getenv("TELEGRAM_BOT_TOKEN")
    env_chat  = os.getenv("TELEGRAM_CHAT_ID")
    if env_token:
        data["telegram"]["bot_token"] = env_token
    if env_chat:
        data["telegram"]["chat_id"] = env_chat

    return Config(data)
