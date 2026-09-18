"""Global rotating application log plus a per-session app.log handler."""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from .config import APP_DIR_NAME

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(base) / APP_DIR_NAME / "logs"


def setup_logging(console: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(d / "app.log", maxBytes=2_000_000,
                                                 backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(h)
    except OSError:
        pass
    if console:
        c = logging.StreamHandler()
        c.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(c)
    logging.getLogger("bleak").setLevel(logging.WARNING)


def attach_session_log(folder: Path) -> logging.Handler | None:
    try:
        h = logging.FileHandler(folder / "app.log", encoding="utf-8")
    except OSError as e:
        logging.getLogger(__name__).warning("session app.log unavailable: %s", e)
        return None
    h.setFormatter(logging.Formatter(FORMAT))
    logging.getLogger().addHandler(h)
    return h


def detach_session_log(h: logging.Handler | None) -> None:
    if h is not None:
        logging.getLogger().removeHandler(h)
        h.close()
