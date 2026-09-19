"""Desktop UI: a WebView2 window (pywebview) driven by the session controller.

The page in ``web/`` renders everything; this module owns the BLE session, keeps the
chart buffers, and exposes one small API to JavaScript. The page polls ``poll()`` a few
times a second instead of Python pushing updates, which keeps the bridge one-way and
makes a slow or busy renderer harmless.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

import webview

from . import __version__, pmd
from .config import AppConfig, load_config, save_config
from .logsetup import setup_logging
from .metrics import METHOD_NOTE, WINDOW_CHOICES
from .recover import recover_interrupted
from .session import Event, LogOptions, SessionController, Snapshot, State, StreamConfig
from .storage import validate_participant
from .transport import BleTransport, BluetoothUnavailable, ConnectError, DeviceInfo, FakeTransport

log = logging.getLogger(__name__)

APP_NAME = "PolarH10 Logging"
WEB_DIR = Path(__file__).with_name("web")
HR_SPAN_S = 300.0  # HR & RR chart window
ECG_SPAN_S = 5.0
ACC_SPAN_S = 20.0
MAX_POINTS = 900  # more than the chart is wide in pixels
EVENT_KEEP = 40
SCAN_TIMEOUT_S = 10.0  # the connect screen promises "about 10 seconds"
CLOSE_TIMEOUT_S = 8.0


class AsyncRunner:
    """One background thread owning an asyncio loop."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="ble-loop", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, fn, *args) -> concurrent.futures.Future:
        """Run a plain function on the loop thread and return its result as a future."""
        async def _wrap():
            return fn(*args)
        return self.submit(_wrap())

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


def _thin(points: list, limit: int = MAX_POINTS) -> list:
    """Keep at most ``limit`` points, evenly spaced, always including the newest."""
    n = len(points)
    if n <= limit:
        return points
    step = n / limit
    return [points[min(n - 1, int(i * step))] for i in range(limit)]


class Api:
    """Everything the page can call. Methods return immediately; state arrives via poll()."""

    def __init__(self, fake: bool = False) -> None:
        self.cfg: AppConfig = load_config()
        self.events: queue.Queue[Event] = queue.Queue()
        self.runner = AsyncRunner()
        transport = FakeTransport() if fake else BleTransport()
        self.ctrl = SessionController(transport, self.events.put, grace_s=self.cfg.grace_s)
        self.ctrl.stream_config = StreamConfig(self.cfg.ecg, self.cfg.acc, self.cfg.acc_rate_hz,
                                               self.cfg.acc_range_g)
        self._window: Any = None
        self.snap = Snapshot()
        self.devices: list[DeviceInfo] = []
        self.scanning = False
        self.scan_started = 0.0
        self.scan_error: str | None = None
        self.connect_error: str | None = None
        self.notice: dict | None = None  # {"kind", "text"} shown as a toast
        self.recovered: list[dict] = []
        self.log_rows: list[dict] = []
        self.last_rr: float | None = None
        self.last_packet_at: float | None = None
        self.saved: dict | None = None  # summary of the recording that just finished
        self.lost: dict | None = None  # set when the grace period ran out
        self.closing = False
        self.ui_ready = False
        self.hr_pts: deque[tuple[float, float]] = deque()
        self.rr_pts: deque[tuple[float, float]] = deque()
        self.ecg_pts: deque[tuple[float, float]] = deque()
        self.acc_pts: dict[str, deque] = {a: deque() for a in "xyz"}
        self.now_s = self.ecg_now = self.acc_now = 0.0
        self.runner.call(self.ctrl.set_window, self.cfg.metric_window)
        threading.Thread(target=self._drain, name="ui-events", daemon=True).start()
        threading.Thread(target=self._startup_recovery, daemon=True).start()

    # --- event pump ----------------------------------------------------------------------
    def _drain(self) -> None:
        while True:
            e = self.events.get()
            try:
                self._apply_event(e)
            except Exception:  # noqa: BLE001 - never kill the pump
                log.exception("event handling failed")

    def _apply_event(self, e: Event) -> None:
        before_logging = self.snap.logging
        self.snap = e.snapshot
        if e.kind == "pmd":
            self._add_pmd(e)
            return
        if e.kind == "packet":
            t = e.t_s or 0.0
            self.now_s = t
            self.last_packet_at = time.monotonic()
            if e.hr is not None:
                self.hr_pts.append((t, float(e.hr)))
            after = 0.0
            beats = []
            for rr in reversed(e.rr_ms):
                beats.append((t - after / 1000, rr))
                after += rr
            self.rr_pts.extend(reversed(beats))
            if e.rr_ms:
                self.last_rr = e.rr_ms[-1]
            cutoff = t - HR_SPAN_S - 10
            for d in (self.hr_pts, self.rr_pts):
                while d and d[0][0] < cutoff:
                    d.popleft()
            return
        if e.kind == "state" and "Connected to" in e.detail:
            self._clear_charts()
            self.lost = None
        if before_logging and not self.snap.logging:
            self._remember_saved()
        if e.kind == "error" and "grace period expired" in e.detail:
            folder = self.snap.last_session_folder
            self.lost = {"saved": before_logging, "folder": str(folder) if folder else None,
                         "name": folder.name if folder else ""}
        if e.detail:
            self._add_row(e.kind, e.detail)

    def _add_pmd(self, e: Event) -> None:
        if not e.times_s:
            return
        if e.stream == pmd.ECG:
            self.ecg_pts.extend(zip(e.times_s, e.values))
            self.ecg_now = e.times_s[-1]
            cutoff = self.ecg_now - ECG_SPAN_S - 1
            while self.ecg_pts and self.ecg_pts[0][0] < cutoff:
                self.ecg_pts.popleft()
        elif e.stream == pmd.ACC:
            for axis, i in (("x", 0), ("y", 1), ("z", 2)):
                self.acc_pts[axis].extend((t, v[i]) for t, v in zip(e.times_s, e.values))
            self.acc_now = e.times_s[-1]
            cutoff = self.acc_now - ACC_SPAN_S - 1
            for d in self.acc_pts.values():
                while d and d[0][0] < cutoff:
                    d.popleft()

    def _clear_charts(self) -> None:
        self.hr_pts.clear()
        self.rr_pts.clear()
        self.ecg_pts.clear()
        for d in self.acc_pts.values():
            d.clear()
        self.now_s = self.ecg_now = self.acc_now = 0.0
        self.last_rr = None

    def _add_row(self, kind: str, detail: str) -> None:
        self.log_rows.append({"time": time.strftime("%H:%M:%S"), "kind": kind, "text": detail})
        del self.log_rows[:-EVENT_KEEP]

    def _remember_saved(self) -> None:
        """Snapshot the just-finished recording so the page can show the Saved card."""
        folder = self.snap.last_session_folder
        if folder is None:
            return
        self.saved = {
            "folder": str(folder),
            "name": folder.name,
            "participant": folder.name.rsplit("_", 2)[0],
            "at": time.strftime("%H:%M:%S"),
            "bytes": _folder_size(folder),
        }

    def _startup_recovery(self) -> None:
        try:
            done = recover_interrupted(Path(self.cfg.output_root))
        except Exception as e:  # noqa: BLE001
            log.warning("startup recovery failed: %s", e)
            return
        for folder in done:
            self.recovered.append({"folder": str(folder), "name": folder.name})
            self._add_row("warning", f"Recovered interrupted session {folder.name}")

    # --- state for the page --------------------------------------------------------------
    def poll(self, tab: str = "hr") -> dict:
        if not self.ui_ready:  # first call from the page: the window really did load
            self.ui_ready = True
            log.info("UI ready")
        s = self.snap
        dev, det = s.device, s.details
        live = s.state in (State.CONNECTED, State.RECONNECTING)
        out = {
            "version": __version__,
            "state": s.state.value,
            "logging": s.logging,
            "scanning": self.scanning,
            "scanElapsed": time.monotonic() - self.scan_started if self.scanning else 0.0,
            "scanError": self.scan_error,
            "connectError": self.connect_error,
            "notice": self.notice,
            "device": _device(dev),
            "deviceDetails": {"model": det.model, "firmware": det.firmware} if det else None,
            "devices": [_device(d) for d in self.devices],
            "lastDeviceId": self.cfg.last_device_id,
            "battery": s.battery,
            "contact": s.contact,
            "hr": s.window.latest_hr if s.window else None,
            "lastRr": self.last_rr,
            "live": live,
            "lastSeenS": (time.monotonic() - self.last_packet_at) if self.last_packet_at
            else None,
            "metrics": _metrics(s),
            "windowLabel": s.window_label,
            "windowChoices": list(WINDOW_CHOICES),
            "methodNote": METHOD_NOTE,
            "connectAttempt": s.connect_attempt,
            "connectAttempts": s.connect_attempts,
            "reconnectAttempt": s.reconnect_attempt,
            "reconnectLeftS": s.reconnect_left_s,
            "graceS": self.ctrl.grace_s,
            "failure": s.failure,
            "streams": {
                "ecg": s.stream_config.ecg,
                "acc": s.stream_config.acc,
                "accRate": s.stream_config.acc_rate_hz,
                "accRange": s.stream_config.acc_range_g,
                "active": sorted(s.streams_active),
                "available": sorted(s.streams_available),
            },
            "session": {
                "folder": str(s.session_folder) if s.session_folder else None,
                "elapsedS": s.elapsed_s,
                "packets": s.packets,
                "rr": s.rr,
                "rrExcluded": s.rr_excluded,
                "disconnects": s.disconnects,
                "ecgSamples": s.ecg_samples,
                "accSamples": s.acc_samples,
                "bytes": _folder_size(s.session_folder) if s.session_folder else 0,
            },
            "saved": self.saved,
            "lostNotice": self.lost,
            "events": self.log_rows[::-1][:12],
            "recovered": self.recovered,
            "settings": self.settings(),
            "series": self._series(tab),
        }
        return out

    def _series(self, tab: str) -> dict:
        if tab == "ecg":
            return {"tab": "ecg", "now": self.ecg_now, "span": ECG_SPAN_S,
                    "ecg": _thin(list(self.ecg_pts))}
        if tab == "acc":
            return {"tab": "acc", "now": self.acc_now, "span": ACC_SPAN_S,
                    "x": _thin(list(self.acc_pts["x"])), "y": _thin(list(self.acc_pts["y"])),
                    "z": _thin(list(self.acc_pts["z"]))}
        return {"tab": "hr", "now": self.now_s, "span": HR_SPAN_S,
                "hr": _thin(list(self.hr_pts)), "rr": _thin(list(self.rr_pts))}

    def settings(self) -> dict:
        c = self.cfg
        return {"outputRoot": c.output_root, "graceS": c.grace_s, "theme": c.theme,
                "formats": list(c.formats), "metricWindow": c.metric_window,
                "autoRescan": c.auto_rescan}

    # --- actions -------------------------------------------------------------------------
    def scan(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.scan_started = time.monotonic()
        self.scan_error = None
        self._add_row("info", "Scanning for straps nearby...")
        fut = self.runner.submit(self.ctrl.scan(SCAN_TIMEOUT_S))

        def done(f: concurrent.futures.Future) -> None:
            self.scanning = False
            err = f.exception()
            if err is not None:
                self.scan_error = ("Bluetooth is unavailable. Turn Bluetooth on in Windows "
                                   "settings." if isinstance(err, BluetoothUnavailable)
                                   else f"Scan failed: {err}")
                self._add_row("error", self.scan_error)
                return
            self.devices = sorted(f.result(), key=lambda d: d.rssi or -999, reverse=True)
            self._add_row("info", f"Scan found {len(self.devices)} strap(s)")
        fut.add_done_callback(done)

    def connect(self, device_id: str = "") -> None:
        dev = next((d for d in self.devices if d.device_id == device_id), None)
        if dev is None and device_id:
            self.connect_error = "That strap is no longer in the list. Scan again."
            return
        if dev is None:
            dev = next((d for d in self.devices if d.device_id == self.cfg.last_device_id),
                       self.devices[0] if self.devices else None)
        if dev is None:
            self.scan()
            return
        self.connect_error = None
        self.lost = None
        self.cfg.last_device_id = dev.device_id
        save_config(self.cfg)
        self.ctrl.grace_s = self.cfg.grace_s
        fut = self.runner.submit(self.ctrl.connect(dev))

        def done(f: concurrent.futures.Future) -> None:
            err = f.exception()
            if err is None:
                return
            self.connect_error = (str(err) if isinstance(err, ConnectError)
                                  else f"Could not connect: {err}")
        fut.add_done_callback(done)

    def disconnect(self) -> None:
        self.runner.submit(self.ctrl.disconnect())

    def retry_now(self) -> None:
        self.runner.call(self.ctrl.retry_now)

    def extend_grace(self, seconds: int = 120) -> None:
        self.runner.call(self.ctrl.extend_grace, float(seconds))

    def set_window(self, label: str) -> None:
        self.runner.call(self.ctrl.set_window, label)
        self.cfg.metric_window = label
        save_config(self.cfg)

    def set_streams(self, ecg: bool, acc: bool, rate: int = 0, rng: int = 0) -> None:
        cfg = StreamConfig(bool(ecg), bool(acc), int(rate) or self.cfg.acc_rate_hz,
                           int(rng) or self.cfg.acc_range_g)
        self.cfg.ecg, self.cfg.acc = cfg.ecg, cfg.acc
        self.cfg.acc_rate_hz, self.cfg.acc_range_g = cfg.acc_rate_hz, cfg.acc_range_g
        save_config(self.cfg)
        fut = self.runner.submit(self.ctrl.set_streams(cfg))

        def done(f: concurrent.futures.Future) -> None:
            if f.exception():
                self._add_row("error", str(f.exception()))
        fut.add_done_callback(done)

    def check_participant(self, text: str) -> dict:
        try:
            validate_participant(text)
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": ""}

    def start_log(self, participant: str, condition: str = "", notes: str = "") -> dict:
        try:
            pid = validate_participant(participant)
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        self.cfg.condition = condition.strip()
        save_config(self.cfg)
        self.saved = None
        opts = LogOptions(pid, Path(self.cfg.output_root), condition.strip(), notes.strip())
        try:
            self.runner.call(self.ctrl.start_log, opts).result(timeout=10)
        except Exception as e:  # noqa: BLE001 - shown in the card
            self._add_row("error", f"Could not start recording: {e}")
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": ""}

    def stop_log(self) -> None:
        self.runner.call(self.ctrl.stop_log)

    def dismiss_saved(self) -> None:
        self.saved = None

    def dismiss_recovered(self) -> None:
        self.recovered = []

    def dismiss_notice(self) -> None:
        self.notice = None
        self.scan_error = None
        self.connect_error = None
        self.lost = None

    def open_folder(self, folder: str = "") -> None:
        target = Path(folder) if folder else (self.snap.session_folder
                                              or self.snap.last_session_folder)
        if target and Path(target).exists():
            _open_in_explorer(Path(target))

    def browse_output(self) -> str:
        if self._window is None:
            return self.cfg.output_root
        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG,
                                                directory=self.cfg.output_root)
        if picked:
            self.cfg.output_root = str(Path(picked[0]))
            save_config(self.cfg)
        return self.cfg.output_root

    def save_settings(self, data: dict) -> dict:
        grace = int(data.get("graceS", self.cfg.grace_s))
        if not 0 <= grace <= 86400:
            return {"ok": False, "message": "Grace period must be between 0 and 86400 seconds."}
        root = str(data.get("outputRoot") or self.cfg.output_root)
        self.cfg.output_root = root
        self.cfg.grace_s = grace
        self.cfg.theme = str(data.get("theme", self.cfg.theme))
        self.cfg.auto_rescan = bool(data.get("autoRescan", self.cfg.auto_rescan))
        formats = [f for f in data.get("formats", self.cfg.formats) if f in ("csv",)]
        self.cfg.formats = formats or ["csv"]
        save_config(self.cfg)
        self.ctrl.grace_s = grace
        return {"ok": True, "message": ""}

    def open_bluetooth_settings(self) -> None:
        if sys.platform == "win32":
            os.startfile("ms-settings:bluetooth")  # noqa: S606

    # --- window chrome -------------------------------------------------------------------
    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def toggle_maximize(self) -> None:
        if not self._window:
            return
        if getattr(self._window, "maximized", False):
            self._window.restore()
        else:
            self._window.maximize()

    def close(self) -> None:
        if self._window:
            self._window.destroy()

    # --- shutdown ------------------------------------------------------------------------
    def shutdown(self) -> bool:
        """Called when the window closes: stop the log and disconnect, then let it go."""
        if self.closing:
            return True
        self.closing = True
        save_config(self.cfg)
        if self.snap.state != State.IDLE:
            fut = self.runner.submit(self.ctrl.shutdown())
            try:
                fut.result(timeout=CLOSE_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                log.warning("shutdown did not finish cleanly")
        self.runner.stop()
        return True


def _device(d: DeviceInfo | None) -> dict | None:
    if d is None:
        return None
    return {"name": d.name, "deviceId": d.device_id, "address": d.address, "rssi": d.rssi}


def _metrics(s: Snapshot) -> dict:
    w = s.window
    if w is None:
        return {}
    out = {k: v for k, v in asdict(w).items() if isinstance(v, (int, float, type(None)))}
    # how much data the window actually covers, for the "Based on ..." line
    out["seconds"] = (w.rr_used * w.rr_mean / 1000) if w.rr_used and w.rr_mean else None
    return out


def _folder_size(folder: Path | None) -> int:
    if folder is None:
        return 0
    try:
        return sum(f.stat().st_size for f in folder.glob("*") if f.is_file())
    except OSError:
        return 0


def _open_in_explorer(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def main() -> None:
    setup_logging()
    log.info("web UI start %s", __version__)
    fake = "--fake" in sys.argv or os.environ.get("POLARH10_FAKE") == "1"
    api = Api(fake=fake)
    window = webview.create_window(APP_NAME, str(WEB_DIR / "index.html"), js_api=api,
                                   width=1280, height=800, min_size=(1024, 700),
                                   frameless=True, easy_drag=False, background_color="#f3f4f6")
    api._window = window
    window.events.closing += api.shutdown
    webview.start(gui="edgechromium" if sys.platform == "win32" else None)


if __name__ == "__main__":
    main()

