"""Remembered settings in %APPDATA%/PolarH10Logging/config.json."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)
APP_DIR_NAME = "PolarH10Logging"


def config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME / "config.json"


def default_output_root() -> Path:
    return Path.home() / "Documents" / APP_DIR_NAME


@dataclass
class AppConfig:
    output_root: str = ""
    grace_s: int = 120
    metric_window: str = "60 s"
    last_device_id: str = ""
    condition: str = ""
    ecg: bool = False
    acc: bool = False
    acc_rate_hz: int = 50
    acc_range_g: int = 8

    def __post_init__(self) -> None:
        if not self.output_root:
            self.output_root = str(default_output_root())


def load_config(path: Path | None = None) -> AppConfig:
    path = path or config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(AppConfig)}
        cfg = AppConfig(**{k: v for k, v in data.items() if k in known})
        cfg.grace_s = max(0, min(86400, int(cfg.grace_s)))
        if cfg.acc_rate_hz not in (25, 50, 100, 200):
            cfg.acc_rate_hz = 50
        if cfg.acc_range_g not in (2, 4, 8):
            cfg.acc_range_g = 8
        return cfg
    except FileNotFoundError:
        return AppConfig()
    except (OSError, ValueError, TypeError) as e:
        log.warning("config unreadable, using defaults: %s", e)
        return AppConfig()


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    path = path or config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("could not save config: %s", e)
