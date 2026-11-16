"""Settings and log locations: %APPDATA%\\Mediatheque on Windows,
~/Library/Application Support/Mediatheque on a Mac."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

APP_NAME = "Mediatheque"


def data_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_file(key: str) -> Path:
    return log_dir() / f"{key}.log"


def default_download_dir() -> str:
    return str(Path.home() / "Downloads")


def setup_logging(platform_keys: list[str]) -> None:
    """One log file per platform, so a broken tab's history is easy to read."""
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    def handler(path: Path) -> logging.Handler:
        h = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        h.setFormatter(fmt)
        return h

    app_log = logging.getLogger("mediatheque")
    app_log.setLevel(logging.DEBUG)
    app_log.addHandler(handler(log_file("app")))
    for key in platform_keys:
        log = logging.getLogger(f"mediatheque.{key}")
        log.addHandler(handler(log_file(key)))
        log.propagate = False


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "settings.json"
        self.data: dict = {"folders": [], "tabs": {}, "window": {}}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def tab(self, key: str) -> dict:
        return self.data["tabs"].setdefault(key, {})

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            logging.getLogger("mediatheque").exception("Could not save settings")
