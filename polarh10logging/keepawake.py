"""Prevent Windows system sleep while a log is active (display may still turn off)."""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    """Call acquire/release from the same thread; SetThreadExecutionState is per thread."""

    def __init__(self) -> None:
        self.active = False

    def _set(self, flags: int) -> None:
        if sys.platform != "win32":
            return
        import ctypes
        if not ctypes.windll.kernel32.SetThreadExecutionState(flags):
            log.warning("SetThreadExecutionState(%#x) failed", flags)

    def acquire(self) -> None:
        if not self.active:
            self._set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            self.active = True

    def release(self) -> None:
        if self.active:
            self._set(ES_CONTINUOUS)
            self.active = False
