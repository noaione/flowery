"""User configuration and on-disk locations."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["Session", "UserConfig", "config_dir", "config_path", "load_config", "save_config"]

APP_NAME = "flowery"


def config_dir() -> Path:
    """Return the per-user directory holding the CLI configuration."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_NAME


def config_path() -> Path:
    """Return the path of the JSON file holding the persisted session."""
    return config_dir() / "user.json"


class Session(BaseModel):
    """Persisted auth state for a single account."""

    email: str | None = None
    user_id: str | None = None
    display_name: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: int | None = Field(
        default=None,
        description="Unix timestamp (seconds) at which ``access_token`` stops being valid.",
    )

    @property
    def authenticated(self) -> bool:
        return bool(self.access_token)


class UserConfig(BaseModel):
    """Root of ``user.json``."""

    session: Session = Field(default_factory=Session)
    download_dir: str | None = Field(
        default=None,
        description="Override for the output root. Relative paths resolve against the CWD.",
    )

    # -- helpers ---------------------------------------------------------------
    def output_root(self) -> Path:
        """Return the directory new downloads are written into."""
        if self.download_dir:
            return Path(self.download_dir).expanduser()
        return Path.cwd() / "DOWNLOADS"


def load_config() -> UserConfig:
    """Read the user configuration, falling back to an empty one."""
    path = config_path()
    if not path.is_file():
        return UserConfig()
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UserConfig()
    try:
        return UserConfig.model_validate(raw)
    except Exception:  # pragma: no cover - corrupted config should not brick the CLI
        return UserConfig()


def save_config(config: UserConfig) -> Path:
    """Write the user configuration to disk and return the path written."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if sys.platform != "win32":
        path.chmod(0o600)
    return path
