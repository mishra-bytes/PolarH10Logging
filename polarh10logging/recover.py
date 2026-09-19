"""Finish sessions left in `recording` state by a crash or power loss."""

from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path
from typing import Any

from . import __version__
from .metrics import METHOD_NOTE, Metrics
from .storage import SESSION_JSON, SUMMARY_CSV, SUMMARY_KEYS, StorageError, iso_local, \
    write_json_atomic

log = logging.getLogger(__name__)


def _read_meta(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / SESSION_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _hr_csv(folder: Path) -> Path | None:
    return next(iter(sorted(folder.glob("HR_*.csv"))), None)


def _complete_rows(folder: Path, stream: str) -> int:
    """Data rows in a stream CSV, ignoring the header and a torn final line."""
    total = 0
    for path in folder.glob(f"{stream}_*.csv"):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        total += max(0, data.count(b"\n") - 1)
    return total


def find_unfinished(root: Path) -> list[Path]:
    out = []
    try:
        folders = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []
    for folder in sorted(folders):
        meta = _read_meta(folder)
        if meta is None:
            if _hr_csv(folder):
                out.append(folder)
        elif meta.get("status") == "recording":
            out.append(folder)
    return out


def recover_session(folder: Path) -> dict[str, Any]:
    """Rebuild counts, summary and metadata from the HR CSV; mark the session interrupted."""
    csv_path = _hr_csv(folder)
    if csv_path is None:
        raise StorageError(f"No HR CSV in {folder}")
    meta = _read_meta(folder) or {}
    m = Metrics()
    counts = {"packets": 0, "rr": 0, "rr_excluded": 0, "errors": 0, "disconnects": 0,
              "segments": 1}
    events: list[dict[str, str]] = []
    first = last = None
    header: dict[str, str] = {}
    packet: tuple[str, int, int, list[float]] | None = None  # (seq, elapsed, hr, rrs)

    def flush_packet() -> None:
        if packet is not None:
            m.add_packet(packet[1], packet[2], packet[3])

    try:
        with open(csv_path, encoding="utf-8", newline="") as f:
            text = f.read()
    except OSError as e:
        raise StorageError(f"Cannot read {csv_path}: {e}") from e
    lines = text.split("\n")
    if lines and lines[-1] != "":
        lines = lines[:-1]  # torn final line from an interrupted write
    for row in csv.DictReader(lines):
        if None in row or row.get("pc_time_unix_ms") in (None, ""):
            continue
        header = header or row
        t = int(row["pc_time_unix_ms"])
        first = t if first is None else first
        last = t
        seg = int(row["segment"] or 1)
        counts["segments"] = max(counts["segments"], seg)
        if row["event"]:
            events.append({"time_iso": row["pc_time_iso"], "kind": row["event"],
                           "detail": row["detail"]})
            if row["event"] == "disconnect":
                counts["disconnects"] += 1
                flush_packet()
                packet = None
                m.break_chain()
            elif row["event"] == "error":
                counts["errors"] += 1
            continue
        if packet is None or packet[0] != row["packet_seq"]:
            flush_packet()
            counts["packets"] += 1
            packet = (row["packet_seq"], int(row["elapsed_ms"]), int(row["hr_bpm"]), [])
        if row["rr_ms"]:
            packet[3].append(float(row["rr_ms"]))
    flush_packet()
    counts["rr"], counts["rr_excluded"] = m.rr_count, m.rr_excluded
    for name in ("ECG", "ACC"):
        counts[f"{name.lower()}_samples"] = _complete_rows(folder, name)
    full = m.window(None)
    duration = (last - first) / 1000 if first is not None else 0.0
    meta.update({
        "schema_version": 4, "status": "interrupted", "end_reason": "process_interrupted",
        "session_id": meta.get("session_id") or header.get("session_id") or folder.name,
        "participant_id": meta.get("participant_id") or header.get("participant_id"),
        "stop_time_iso": iso_local(last) if last else None, "stop_time_unix_ms": last,
        "duration_s": round(duration, 3), "counts": counts,
        "recent_events": events[-50:], "recovered_by": __version__,
    })
    meta.setdefault("start_time_unix_ms", first)
    meta.setdefault("start_time_iso", iso_local(first) if first else None)
    meta.setdefault("device", {})

    def fmt(v: float | None) -> str:
        return "" if v is None else f"{v:.1f}"
    dev = meta.get("device") or {}
    vals = {
        "session_id": meta["session_id"], "participant_id": meta["participant_id"],
        "condition": meta.get("condition", ""), "status": "interrupted",
        "end_reason": "process_interrupted", "start_time_iso": meta.get("start_time_iso"),
        "stop_time_iso": meta["stop_time_iso"], "duration_s": f"{duration:.3f}",
        "connected_pct": "", "device_id": dev.get("device_id"), "model": dev.get("model"),
        "firmware": dev.get("firmware"), "battery_start": meta.get("battery_start"),
        "battery_end": meta.get("battery_end"), "packets": counts["packets"],
        "rr_count": counts["rr"], "rr_used": full.rr_used, "rr_excluded": counts["rr_excluded"],
        "disconnects": counts["disconnects"], "hr_min": m.full.hr_min,
        "hr_mean": fmt(full.hr_mean), "hr_max": m.full.hr_max, "rr_mean_ms": fmt(full.rr_mean),
        "sdnn_ms": fmt(full.sdnn), "rmssd_ms": fmt(full.rmssd), "pnn50_pct": fmt(full.pnn50),
        "method_note": METHOD_NOTE, "app_version": meta.get("app_version", __version__),
        "ecg_samples": counts["ecg_samples"], "acc_samples": counts["acc_samples"],
    }
    try:
        with open(folder / SUMMARY_CSV, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["metric", "value"])
            w.writerows((k, "" if vals[k] is None else str(vals[k])) for k in SUMMARY_KEYS)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        raise StorageError(f"Could not write summary.csv: {e}") from e
    meta["files"] = sorted({p.name for p in folder.iterdir() if not p.name.endswith(".tmp")}
                           | {SESSION_JSON})
    write_json_atomic(folder / SESSION_JSON, meta)
    log.info("recovered interrupted session %s", folder)
    return meta


def recover_interrupted(root: Path) -> list[Path]:
    done = []
    for folder in find_unfinished(root):
        try:
            recover_session(folder)
            done.append(folder)
        except StorageError as e:
            log.warning("recovery failed for %s: %s", folder, e)
    return done
