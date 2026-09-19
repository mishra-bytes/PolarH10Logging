"""Session folder, durable HR/RR CSV, raw JSONL, atomic metadata, summary."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__
from . import pmd
from .hr_parser import HrSample
from .metrics import METHOD_NOTE, WindowMetrics, beat_offsets, rr_accepted

CSV_HEADER = [
    "session_id", "participant_id", "pc_time_iso", "pc_time_utc_iso", "pc_time_unix_ms",
    "elapsed_ms", "packet_seq", "segment", "hr_bpm", "rr_index", "rr_ms",
    "beat_time_est_unix_ms", "beat_elapsed_est_ms", "contact", "event", "detail",
]
ECG_HEADER = ["sample_time_utc_iso", "sample_time_unix_ms", "sensor_time_ns",
              "pc_received_unix_ms", "frame_seq", "segment", "ecg_uv"]
ACC_HEADER = ["sample_time_utc_iso", "sample_time_unix_ms", "sensor_time_ns",
              "pc_received_unix_ms", "frame_seq", "segment", "x_mg", "y_mg", "z_mg"]
SUMMARY_KEYS = [
    "session_id", "participant_id", "condition", "status", "end_reason", "start_time_iso",
    "stop_time_iso", "duration_s", "connected_pct", "device_id", "model", "firmware",
    "battery_start", "battery_end", "packets", "rr_count", "rr_used", "rr_excluded",
    "disconnects", "hr_min", "hr_mean", "hr_max", "rr_mean_ms", "sdnn_ms", "rmssd_ms",
    "pnn50_pct", "method_note", "app_version", "ecg_samples", "acc_samples",
]
SCHEMA_VERSION = 4
RAW_SCHEMA_VERSION = 1
FSYNC_EVERY_MS = 5000
CHECKPOINT_EVERY_MS = 30000
MIN_FREE_BYTES = 250 * 1024 * 1024
PARTICIPANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SESSION_JSON = "session.json"
SUMMARY_CSV = "summary.csv"
RAW_JSONL = "raw.jsonl"


class StorageError(Exception):
    """Any failure to create, write, sync, or replace a session file."""


@dataclass
class Clock:
    """Wall-clock and monotonic milliseconds; replaced by a fake in tests."""
    wall_ms: Callable[[], int] = field(default=lambda: time.time_ns() // 1_000_000)
    mono_ms: Callable[[], int] = field(default=lambda: time.monotonic_ns() // 1_000_000)


def iso_local(unix_ms: int) -> str:
    return datetime.fromtimestamp(unix_ms / 1000).astimezone().isoformat(timespec="milliseconds")


def iso_utc(unix_ms: int) -> str:
    dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{unix_ms % 1000:03d}Z"


def iso_utc_float(unix_ms: float) -> str:
    return iso_utc(int(unix_ms // 1))


def local_offset(unix_ms: int) -> str:
    s = datetime.fromtimestamp(unix_ms / 1000).astimezone().strftime("%z")
    return f"{s[:3]}:{s[3:]}"


def validate_participant(pid: str) -> str:
    pid = pid.strip()
    if not PARTICIPANT_RE.match(pid):
        raise ValueError("Participant ID must be 1-64 characters: letters, digits, _ or -")
    return pid


def check_output_root(root: Path) -> None:
    """Raise StorageError unless root is writable with enough free space."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".write_test_{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
        free = shutil.disk_usage(root).free
    except OSError as e:
        raise StorageError(f"Output folder is not writable: {root} ({e})") from e
    if free < MIN_FREE_BYTES:
        raise StorageError(f"Less than 250 MiB free in {root}")


def write_json_atomic(path: Path, doc: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        raise StorageError(f"Could not write {path.name}: {e}") from e


def _fmt(v: float | None, nd: int = 1) -> str:
    return "" if v is None else f"{v:.{nd}f}"


class SessionWriter:
    """Owns one session folder. Every public write raises StorageError on failure."""

    def __init__(self, root: Path, participant_id: str, *, device: dict[str, Any],
                 condition: str = "", notes: str = "", grace_s: int = 120,
                 clock: Clock | None = None, battery: int | None = None) -> None:
        self.clock = clock or Clock()
        self.participant_id = validate_participant(participant_id)
        check_output_root(root)
        self.start_wall = self.clock.wall_ms()
        self.start_mono = self.clock.mono_ms()
        stamp = datetime.fromtimestamp(self.start_wall / 1000).strftime("%Y%m%d_%H%M%S")
        base = f"{self.participant_id}_{stamp}"
        folder, n = root / base, 1
        while folder.exists():
            n += 1
            folder = root / f"{base}_{n}"
        try:
            folder.mkdir(parents=True)
        except OSError as e:
            raise StorageError(f"Could not create session folder {folder}: {e}") from e
        self.folder = folder
        self.session_id = folder.name
        self.csv_name = f"HR_{self.participant_id}_{stamp}.csv"
        self.device = dict(device)
        self.condition, self.notes, self.grace_s = condition, notes, grace_s
        self.battery_start = self.battery_end = battery
        self.status = "recording"
        self.end_reason: str | None = None
        self.stop_wall: int | None = None
        self.segment = 1
        self.counts = {"packets": 0, "rr": 0, "rr_excluded": 0, "errors": 0,
                       "disconnects": 0, "segments": 1, "ecg_frames": 0, "ecg_samples": 0,
                       "acc_frames": 0, "acc_samples": 0}
        self.streams: dict[str, dict[str, Any]] = {}
        self._stamp = stamp
        self._stream_files: dict[int, Any] = {}
        self._stream_csv: dict[int, Any] = {}
        self.recent_events: deque[dict[str, str]] = deque(maxlen=50)
        self._connected_ms = 0
        self._connected_since: int | None = self.start_mono
        self._last_fsync = self.start_mono
        self._last_checkpoint = self.start_mono
        self._closed = False
        try:
            self._csv_f = open(folder / self.csv_name, "w", encoding="utf-8", newline="")
            self._raw_f = open(folder / RAW_JSONL, "w", encoding="utf-8", newline="\n")
        except OSError as e:
            raise StorageError(f"Could not open session files: {e}") from e
        self._csv = csv.writer(self._csv_f, lineterminator="\n")
        self._guard(self._csv.writerow, CSV_HEADER)
        self.write_event("start", f"app {__version__}")
        self.checkpoint(force=True)

    # --- low-level -------------------------------------------------------------------------
    def _guard(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except OSError as e:
            raise StorageError(f"Write failed in {self.folder}: {e}") from e

    def _files(self) -> list[Any]:
        return [self._csv_f, self._raw_f, *self._stream_files.values()]

    def _flush(self, mono: int) -> None:
        for f in self._files():
            self._guard(f.flush)
        if mono - self._last_fsync >= FSYNC_EVERY_MS:
            self._fsync()
            self._last_fsync = mono

    def _fsync(self) -> None:
        for f in self._files():
            self._guard(os.fsync, f.fileno())

    def _base_row(self, wall: int, mono: int) -> list[Any]:
        return [self.session_id, self.participant_id, iso_local(wall), iso_utc(wall), wall,
                mono - self.start_mono]

    # --- writes ----------------------------------------------------------------------------
    def write_packet(self, wall: int, mono: int, payload: bytes, sample: HrSample | None,
                     char: str = "2A37") -> None:
        """Raw record first, then CSV rows. `sample` is None for an unparsable payload."""
        raw = {"schema_version": RAW_SCHEMA_VERSION, "pc_time_unix_ms": wall,
               "elapsed_ms": mono - self.start_mono, "device_id": self.device.get("device_id"),
               "characteristic": char, "payload_hex": payload.hex()}
        self._guard(self._raw_f.write, json.dumps(raw) + "\n")
        if sample is not None:
            self.counts["packets"] += 1
            seq = self.counts["packets"]
            base = self._base_row(wall, mono)
            contact = "" if sample.contact is None else int(sample.contact)
            elapsed = mono - self.start_mono
            rr_ms = sample.rr_ms
            if not rr_ms:
                self._guard(self._csv.writerow, base + [seq, self.segment, sample.hr_bpm,
                                                        "", "", "", "", contact, "", ""])
            beats = beat_offsets(elapsed, rr_ms)
            for i, (rr, beat_el) in enumerate(zip(rr_ms, beats)):
                self.counts["rr"] += 1
                if not rr_accepted(rr):
                    self.counts["rr_excluded"] += 1
                self._guard(self._csv.writerow, base + [
                    seq, self.segment, sample.hr_bpm, i, f"{rr:.3f}",
                    wall - (elapsed - beat_el), beat_el, contact, "", ""])
        self._flush(mono)
        self.maybe_checkpoint(mono)

    def _stream_writer(self, kind: int):
        w = self._stream_csv.get(kind)
        if w is None:
            name = f"{pmd.STREAM_NAMES[kind]}_{self.participant_id}_{self._stamp}.csv"
            try:
                f = open(self.folder / name, "w", encoding="utf-8", newline="")
            except OSError as e:
                raise StorageError(f"Could not open {name}: {e}") from e
            self._stream_files[kind] = f
            w = self._stream_csv[kind] = csv.writer(f, lineterminator="\n")
            self._guard(w.writerow, ECG_HEADER if kind == pmd.ECG else ACC_HEADER)
        return w

    def write_pmd(self, wall: int, mono: int, payload: bytes, frame: pmd.PmdFrame | None,
                  times: list[tuple[int, float]]) -> None:
        """Raw record first, then one CSV row per sample in the stream's own file."""
        raw = {"schema_version": RAW_SCHEMA_VERSION, "pc_time_unix_ms": wall,
               "elapsed_ms": mono - self.start_mono, "device_id": self.device.get("device_id"),
               "characteristic": "PMD", "payload_hex": payload.hex()}
        self._guard(self._raw_f.write, json.dumps(raw) + "\n")
        if frame is not None and frame.kind in pmd.STREAM_NAMES:
            name = pmd.STREAM_NAMES[frame.kind].lower()
            self.counts[f"{name}_frames"] += 1
            self.counts[f"{name}_samples"] += len(frame.samples)
            seq = self.counts[f"{name}_frames"]
            w = self._stream_writer(frame.kind)
            rows = []
            for sample, (ns, unix_ms) in zip(frame.samples, times):
                vals = list(sample) if frame.kind == pmd.ACC else [sample]
                rows.append([iso_utc_float(unix_ms), f"{unix_ms:.3f}", ns, wall, seq,
                             self.segment, *vals])
            self._guard(w.writerows, rows)
        self._flush(mono)
        self.maybe_checkpoint(mono)

    def set_stream(self, name: str, config: dict[str, Any] | None) -> None:
        """Record a stream start (config) or stop (None) in metadata and the event log."""
        if config is None:
            if name in self.streams:
                self.streams[name]["active"] = False
            self.write_event("stream", f"{name} stopped")
        else:
            self.streams[name] = {**config, "active": True}
            detail = ", ".join(f"{k}={v}" for k, v in config.items())
            self.write_event("stream", f"{name} started ({detail})" if detail else
                             f"{name} started")

    def write_event(self, kind: str, detail: str = "") -> None:
        wall, mono = self.clock.wall_ms(), self.clock.mono_ms()
        if kind == "error":
            self.counts["errors"] += 1
        self._guard(self._csv.writerow, self._base_row(wall, mono) + [
            "", self.segment, "", "", "", "", "", "", kind, detail])
        self.recent_events.append({"time_iso": iso_local(wall), "kind": kind, "detail": detail})
        self._flush(mono)

    def set_battery(self, pct: int) -> None:
        if self.battery_start is None:
            self.battery_start = pct
        self.battery_end = pct
        self.write_event("battery", str(pct))

    def disconnected(self, detail: str = "") -> None:
        mono = self.clock.mono_ms()
        if self._connected_since is not None:
            self._connected_ms += mono - self._connected_since
            self._connected_since = None
        self.counts["disconnects"] += 1
        self.write_event("disconnect", detail)
        self.segment += 1
        self.counts["segments"] = self.segment
        self._fsync()
        self.checkpoint(force=True)

    def reconnected(self, device: dict[str, Any] | None = None) -> None:
        if device:
            self.device.update({k: v for k, v in device.items() if v is not None})
        self._connected_since = self.clock.mono_ms()
        self.write_event("reconnect", "")
        self.checkpoint(force=True)

    def maybe_checkpoint(self, mono: int) -> None:
        if mono - self._last_checkpoint >= CHECKPOINT_EVERY_MS:
            self.checkpoint(force=True)

    def checkpoint(self, force: bool = False) -> None:
        self._last_checkpoint = self.clock.mono_ms()
        write_json_atomic(self.folder / SESSION_JSON, self.metadata())

    # --- metadata --------------------------------------------------------------------------
    def durations(self) -> tuple[float, float]:
        mono = self.clock.mono_ms()
        connected = self._connected_ms
        if self._connected_since is not None:
            connected += mono - self._connected_since
        return (mono - self.start_mono) / 1000, connected / 1000

    def metadata(self) -> dict[str, Any]:
        duration, connected = self.durations()
        files = sorted(p.name for p in self.folder.iterdir() if not p.name.endswith(".tmp"))
        if SESSION_JSON not in files:
            files = sorted(files + [SESSION_JSON])
        return {
            "schema_version": SCHEMA_VERSION, "app_version": __version__,
            "session_id": self.session_id, "participant_id": self.participant_id,
            "condition": self.condition, "notes": self.notes,
            "status": self.status, "end_reason": self.end_reason,
            "device": {k: self.device.get(k) for k in
                       ("name", "device_id", "address", "model", "firmware", "serial")},
            "battery_start": self.battery_start, "battery_end": self.battery_end,
            "start_time_iso": iso_local(self.start_wall), "start_time_unix_ms": self.start_wall,
            "stop_time_iso": iso_local(self.stop_wall) if self.stop_wall else None,
            "stop_time_unix_ms": self.stop_wall,
            "duration_s": round(duration, 3), "connected_s": round(connected, 3),
            "reconnect_grace_s": self.grace_s, "pc_timezone": local_offset(self.start_wall),
            "counts": dict(self.counts), "streams": self.streams,
            "recent_events": list(self.recent_events),
            "files": files,
        }

    def summary_rows(self, full: WindowMetrics, hr_min: int | None,
                     hr_max: int | None) -> list[tuple[str, str]]:
        duration, connected = self.durations()
        pct = 100.0 * connected / duration if duration > 0 else None
        d = self.device
        vals: dict[str, Any] = {
            "session_id": self.session_id, "participant_id": self.participant_id,
            "condition": self.condition, "status": self.status, "end_reason": self.end_reason,
            "start_time_iso": iso_local(self.start_wall),
            "stop_time_iso": iso_local(self.stop_wall) if self.stop_wall else "",
            "duration_s": f"{duration:.3f}", "connected_pct": _fmt(pct),
            "device_id": d.get("device_id"), "model": d.get("model"),
            "firmware": d.get("firmware"), "battery_start": self.battery_start,
            "battery_end": self.battery_end, "packets": self.counts["packets"],
            "rr_count": self.counts["rr"], "rr_used": full.rr_used,
            "rr_excluded": self.counts["rr_excluded"], "disconnects": self.counts["disconnects"],
            "hr_min": hr_min, "hr_mean": _fmt(full.hr_mean), "hr_max": hr_max,
            "rr_mean_ms": _fmt(full.rr_mean), "sdnn_ms": _fmt(full.sdnn),
            "rmssd_ms": _fmt(full.rmssd), "pnn50_pct": _fmt(full.pnn50),
            "method_note": METHOD_NOTE, "app_version": __version__,
            "ecg_samples": self.counts["ecg_samples"], "acc_samples": self.counts["acc_samples"],
        }
        return [(k, "" if vals[k] is None else str(vals[k])) for k in SUMMARY_KEYS]

    # --- end -------------------------------------------------------------------------------
    def finalize(self, status: str, end_reason: str, full: WindowMetrics,
                 hr_min: int | None, hr_max: int | None) -> None:
        """Best-effort: every step runs even if an earlier one fails; raises the first error."""
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        mono = self.clock.mono_ms()
        if self._connected_since is not None:
            self._connected_ms += mono - self._connected_since
            self._connected_since = None
        self.status, self.end_reason = status, end_reason
        self.stop_wall = self.clock.wall_ms()

        def attempt(fn: Callable[[], Any]) -> None:
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - collect, keep finalizing
                errors.append(e)

        attempt(lambda: self.write_event("stop", end_reason))
        attempt(self._fsync)
        for f in self._files():
            attempt(f.close)
        attempt(lambda: self._write_summary(full, hr_min, hr_max))
        attempt(lambda: self.checkpoint(force=True))
        if errors:
            e = errors[0]
            raise e if isinstance(e, StorageError) else StorageError(str(e))

    def close_quietly(self) -> None:
        for f in self._files():
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass
        self._closed = True

    def _write_summary(self, full: WindowMetrics, hr_min: int | None, hr_max: int | None) -> None:
        path = self.folder / SUMMARY_CSV
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, lineterminator="\n")
                w.writerow(["metric", "value"])
                w.writerows(self.summary_rows(full, hr_min, hr_max))
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise StorageError(f"Could not write summary.csv: {e}") from e
