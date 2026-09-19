"""Connection and logging lifecycle. Runs entirely on one asyncio event loop."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable

from . import pmd
from .hr_parser import HrParseError, HrSample, parse_hr_measurement
from .keepawake import KeepAwake
from .logsetup import attach_session_log, detach_session_log
from .metrics import WINDOW_CHOICES, Metrics, WindowMetrics
from .storage import Clock, SessionWriter, StorageError
from .transport import ConnectError, DeviceDetails, DeviceInfo, Transport

log = logging.getLogger(__name__)
SKIP_MODEL_CHECK_ENV = "POLARH10_SKIP_MODEL_CHECK"
RECONNECT_DELAYS = (2.0, 5.0, 10.0, 15.0)
START_CONNECT_DELAYS = (0.0, 1.0, 2.0)  # three attempts
BATTERY_POLL_S = 60.0


class State(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTING = "disconnecting"


@dataclass(frozen=True)
class StreamConfig:
    """Optional Polar PMD streams. HR/RR always stream."""
    ecg: bool = False
    acc: bool = False
    acc_rate_hz: int = 50
    acc_range_g: int = 8

    def wanted(self) -> dict[int, tuple[int, int]]:
        out: dict[int, tuple[int, int]] = {}
        if self.ecg:
            out[pmd.ECG] = (pmd.ECG_RATE_HZ, 0)
        if self.acc:
            out[pmd.ACC] = (self.acc_rate_hz, self.acc_range_g)
        return out


def _stream_meta(kind: int, rate: int, rng: int) -> dict:
    if kind == pmd.ECG:
        return {"sample_rate_hz": rate, "resolution_bits": pmd.ECG_RESOLUTION, "unit": "uV"}
    return {"sample_rate_hz": rate, "range_g": rng, "resolution_bits": pmd.ACC_RESOLUTION,
            "unit": "mG"}


@dataclass(frozen=True)
class LogOptions:
    participant_id: str
    output_root: Path
    condition: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Snapshot:
    state: State = State.IDLE
    logging: bool = False
    device: DeviceInfo | None = None
    details: DeviceDetails | None = None
    battery: int | None = None
    contact: bool | None = None
    window_label: str = "60 s"
    window: WindowMetrics | None = None
    # logged-session values (None/0 when not logging)
    session_folder: Path | None = None
    elapsed_s: float = 0.0
    connected_pct: float | None = None
    packets: int = 0
    rr: int = 0
    rr_excluded: int = 0
    disconnects: int = 0
    hr_min: int | None = None
    hr_max: int | None = None
    reconnect_left_s: float | None = None
    connect_attempt: int = 0  # attempt number while CONNECTING (0 when not connecting)
    connect_attempts: int = 0  # how many connect attempts are made in total
    reconnect_attempt: int = 0  # attempt number while RECONNECTING
    last_session_folder: Path | None = None
    failure: str | None = None
    streams_available: frozenset = frozenset()
    streams_active: frozenset = frozenset()
    stream_config: StreamConfig = field(default_factory=StreamConfig)
    ecg_samples: int = 0
    acc_samples: int = 0


@dataclass(frozen=True)
class Event:
    kind: str  # state | packet | info | warning | error | log_started | log_stopped
    detail: str = ""
    snapshot: Snapshot = field(default_factory=Snapshot)
    hr: int | None = None
    rr_ms: tuple[float, ...] = ()
    t_s: float | None = None  # seconds since connection start, for charts
    stream: int | None = None  # PMD events: pmd.ECG or pmd.ACC
    values: tuple = ()  # PMD samples: uV, or (x, y, z) mG
    times_s: tuple = ()  # PMD sample times, seconds since connection start


class SessionController:
    def __init__(self, transport: Transport, emit: Callable[[Event], None], *,
                 clock: Clock | None = None, grace_s: int = 120,
                 reconnect_delays: tuple[float, ...] = RECONNECT_DELAYS,
                 start_delays: tuple[float, ...] = START_CONNECT_DELAYS,
                 keep_awake: KeepAwake | None = None) -> None:
        self.transport = transport
        self._emit_cb = emit
        self.clock = clock or Clock()
        self.grace_s = grace_s
        self.reconnect_delays = reconnect_delays
        self.start_delays = start_delays
        self.keep_awake = keep_awake or KeepAwake()
        self.metrics = Metrics()
        self.writer: SessionWriter | None = None
        self._session_log = None
        self.snap = Snapshot()
        self._window_label = "60 s"
        self._conn_mono0 = 0
        self._reconnect_task: asyncio.Task | None = None
        self._battery_task: asyncio.Task | None = None
        self._deadline: float | None = None
        self._retry_signal: asyncio.Event | None = None
        self._last_folder: Path | None = None
        self._failure: str | None = None
        self.stream_config = StreamConfig()
        self._active: dict[int, tuple[int, int]] = {}  # kind -> (rate, range) now streaming
        self._clocks: dict[int, pmd.SampleClock] = {}
        self._pmd_errors: set[int] = set()
        self._conn_wall0 = 0

    # --- helpers ---------------------------------------------------------------------------
    def _refresh(self, **changes) -> Snapshot:
        w = self.writer
        loop_now = asyncio.get_running_loop().time()
        extra: dict = {
            "logging": w is not None,
            "window_label": self._window_label,
            "window": self.metrics.window(WINDOW_CHOICES[self._window_label]),
            "reconnect_left_s": max(0.0, self._deadline - loop_now) if self._deadline else None,
            "last_session_folder": self._last_folder,
            "failure": self._failure,
            "streams_active": frozenset(self._active),
            "stream_config": self.stream_config,
        }
        if w is not None:
            dur, conn = w.durations()
            extra.update(session_folder=w.folder, elapsed_s=dur,
                         connected_pct=100 * conn / dur if dur > 0 else None,
                         packets=w.counts["packets"], rr=w.counts["rr"],
                         rr_excluded=w.counts["rr_excluded"],
                         disconnects=w.counts["disconnects"],
                         hr_min=self.metrics.full.hr_min, hr_max=self.metrics.full.hr_max,
                         ecg_samples=w.counts["ecg_samples"], acc_samples=w.counts["acc_samples"])
        else:
            extra.update(session_folder=None, elapsed_s=0.0, connected_pct=None, packets=0,
                         rr=0, rr_excluded=0, disconnects=0, hr_min=None, hr_max=None,
                         ecg_samples=0, acc_samples=0)
        snap = replace(self.snap, **extra, **changes)
        live = snap.state in (State.CONNECTED, State.RECONNECTING)
        self.snap = replace(snap, streams_available=frozenset(self.transport.pmd_streams())
                            if live else frozenset())
        return self.snap

    def _emit(self, kind: str, detail: str = "", **kw) -> None:
        if kind in ("warning", "error"):
            log.warning("%s: %s", kind, detail)
        elif detail:
            log.info("%s: %s", kind, detail)
        try:
            self._emit_cb(Event(kind, detail, self._refresh(), **kw))
        except Exception:  # noqa: BLE001 - UI problems never break acquisition
            log.exception("emit failed")

    def _log_event(self, kind: str, detail: str = "") -> None:
        """Write an event row to the active log, handling storage failure."""
        if self.writer is None:
            return
        try:
            self.writer.write_event(kind, detail)
        except StorageError as e:
            self._storage_failed(e)

    @property
    def state(self) -> State:
        return self.snap.state

    def set_window(self, label: str) -> None:
        if label in WINDOW_CHOICES:
            self._window_label = label
            self._emit("state")

    # --- scan / connect --------------------------------------------------------------------
    async def scan(self, timeout: float = 8.0) -> list[DeviceInfo]:
        return await self.transport.scan(timeout)

    async def connect(self, device: DeviceInfo) -> None:
        if self.state not in (State.IDLE,):
            raise RuntimeError(f"cannot connect while {self.state.value}")
        self._failure = None
        self._refresh(state=State.CONNECTING, device=device, details=None, battery=None,
                      contact=None, connect_attempt=1,
                      connect_attempts=len(self.start_delays))
        self._emit("state", f"Connecting to {device.name}")
        last: Exception | None = None
        for attempt, delay in enumerate(self.start_delays, 1):
            if delay:
                await asyncio.sleep(delay)
            self._refresh(connect_attempt=attempt)
            try:
                details = await self._connect_once(device, rescan=attempt > 1)
                break
            except ConnectError as e:
                last = e
                self._emit("warning", f"Connect attempt {attempt} failed: {e}")
        else:
            self._refresh(state=State.IDLE, connect_attempt=0)
            self._emit("error", f"Could not connect to {device.name}: {last}")
            raise ConnectError(str(last))
        self.metrics = Metrics()
        self._conn_mono0 = self.clock.mono_ms()
        self._conn_wall0 = self.clock.wall_ms()
        self._active.clear()
        self._refresh(state=State.CONNECTED, details=details, connect_attempt=0)
        self._emit("state", f"Connected to {device.name} (model {details.model or '?'}, "
                            f"firmware {details.firmware or '?'})")
        await self._apply_streams()
        await self._update_battery()
        self._battery_task = asyncio.create_task(self._battery_loop())

    async def _connect_once(self, device: DeviceInfo, rescan: bool) -> DeviceDetails:
        details = await self.transport.connect(device, self._on_packet, self._on_disconnect,
                                               rescan=rescan)
        model = details.model
        if model is None:
            self._emit("warning", "Model Number unreadable; continuing")
        elif "H10" not in model.upper() and not os.environ.get(SKIP_MODEL_CHECK_ENV):
            await self.transport.disconnect()
            raise ConnectError(f"device reports model {model!r}, not an H10")
        return details

    async def _update_battery(self) -> None:
        pct = await self.transport.read_battery()
        if pct is None:
            return
        if not 0 <= pct <= 100:
            self._emit("error", f"Battery value out of range: {pct}")
            self._log_event("error", f"battery out of range: {pct}")
            return
        if pct != self.snap.battery:
            self._refresh(battery=pct)
            if self.writer:
                try:
                    self.writer.set_battery(pct)
                except StorageError as e:
                    self._storage_failed(e)
            self._emit("state")

    async def _battery_loop(self) -> None:
        while True:
            await asyncio.sleep(BATTERY_POLL_S)
            if self.state == State.CONNECTED:
                await self._update_battery()

    # --- optional ECG / accelerometer streams ----------------------------------------------
    async def set_streams(self, cfg: StreamConfig) -> None:
        """Choose ECG/ACC streams. Applied now if connected, otherwise on the next connect."""
        if cfg.acc:
            pmd.start_acc_command(cfg.acc_rate_hz, cfg.acc_range_g)  # validate early
        self.stream_config = cfg
        if self.state == State.CONNECTED:
            await self._apply_streams()
        self._emit("state")

    async def _apply_streams(self) -> None:
        wanted = self.stream_config.wanted()
        available = self.transport.pmd_streams()
        for kind in list(self._active):
            if wanted.get(kind) != self._active[kind]:
                await self.transport.stop_stream(kind)
                del self._active[kind]
                self._stream_event(kind, None)
        for kind, (rate, rng) in wanted.items():
            if kind in self._active:
                continue
            name = pmd.STREAM_NAMES[kind]
            if kind not in available:
                self._emit("warning", f"{name} is not available on this device")
                continue
            try:
                await self.transport.start_stream(kind, rate, rng)
            except (pmd.PmdError, ValueError) as e:
                self._emit("error", f"Could not start {name}: {e}")
                continue
            self._active[kind] = (rate, rng)
            self._clocks[kind] = pmd.SampleClock(rate)
            self._pmd_errors.discard(kind)
            self._stream_event(kind, _stream_meta(kind, rate, rng))
            self._emit("state", f"{name} streaming at {rate} Hz"
                                + (f", range ±{rng} g" if kind == pmd.ACC else ""))

    def _stream_event(self, kind: int, meta: dict | None) -> None:
        if self.writer is None:
            return
        try:
            self.writer.set_stream(pmd.STREAM_NAMES[kind], meta)
        except StorageError as e:
            self._storage_failed(e)

    def _on_pmd(self, payload: bytes, wall: int, mono: int) -> None:
        frame: pmd.PmdFrame | None
        try:
            frame = pmd.parse_frame(payload)
        except pmd.PmdError as e:
            frame = None
            kind = payload[0] if payload else -1
            if kind not in self._pmd_errors:  # report once per stream start
                self._pmd_errors.add(kind)
                self._emit("error", f"Unreadable sensor frame: {e}")
        times: list[tuple[int, float]] = []
        if frame is not None:
            clk = self._clocks.get(frame.kind)
            if clk is None:
                clk = self._clocks[frame.kind] = pmd.SampleClock(
                    pmd.ECG_RATE_HZ if frame.kind == pmd.ECG else 50)
            times = clk.sample_times(frame, wall)
        if self.writer is not None:
            try:
                self.writer.write_pmd(wall, mono, payload, frame, times)
            except StorageError as e:
                self._storage_failed(e)
        if frame is not None:
            w0 = self._conn_wall0
            self._emit("pmd", stream=frame.kind, values=frame.samples,
                       times_s=tuple((t - w0) / 1000 for _, t in times))

    # --- data path -------------------------------------------------------------------------
    def _on_packet(self, char: str, payload: bytes) -> None:
        wall, mono = self.clock.wall_ms(), self.clock.mono_ms()
        if char == "PMD":
            self._on_pmd(payload, wall, mono)
            return
        sample: HrSample | None
        try:
            sample = parse_hr_measurement(payload)
        except HrParseError as e:
            sample = None
            self._emit("error", f"Malformed HR packet {payload.hex()}: {e}")
        if self.writer is not None:
            try:
                self.writer.write_packet(wall, mono, payload, sample, char)
                if sample is None:
                    self.writer.write_event("error", f"malformed packet {payload.hex()}")
            except StorageError as e:
                self._storage_failed(e)
        if sample is None:
            return
        t_ms = mono - self._conn_mono0
        self.metrics.add_packet(t_ms, sample.hr_bpm, sample.rr_ms)
        if sample.contact is not None:
            self._refresh(contact=sample.contact)
        self._emit("packet", hr=sample.hr_bpm, rr_ms=sample.rr_ms, t_s=t_ms / 1000)

    def _on_disconnect(self) -> None:
        if self.state != State.CONNECTED:
            return
        self._reconnect_task = asyncio.get_running_loop().create_task(self._handle_loss())

    async def _handle_loss(self) -> None:
        self.metrics.break_chain()
        self._cancel_battery()
        self._active.clear()
        for clk in self._clocks.values():
            clk.reset()
        if self.writer:
            try:
                self.writer.disconnected("bluetooth link lost")
            except StorageError as e:
                self._storage_failed(e)
        loop = asyncio.get_running_loop()
        self._deadline = loop.time() + self.grace_s
        self._retry_signal = asyncio.Event()
        self._refresh(state=State.RECONNECTING, reconnect_attempt=0)
        self._emit("warning", f"Connection lost; reconnecting for up to {self.grace_s} s")
        device = self.snap.device
        attempt = 0
        try:
            while device is not None and self._deadline and loop.time() < self._deadline:
                delay = self.reconnect_delays[min(attempt, len(self.reconnect_delays) - 1)]
                # never sleep past the deadline: the wait ends when the grace period does
                await self._wait_before_retry(min(delay, self._deadline - loop.time()))
                if not self._deadline or loop.time() >= self._deadline:
                    break
                attempt += 1
                self._refresh(reconnect_attempt=attempt)
                try:
                    details = await self._connect_once(device, rescan=attempt > 1)
                except ConnectError as e:
                    self._emit("state", f"Reconnect attempt {attempt} failed: {e}")
                    continue
                self._deadline = None
                self._refresh(state=State.CONNECTED, details=details, reconnect_attempt=0)
                if self.writer:
                    try:
                        self.writer.reconnected({"model": details.model,
                                                 "firmware": details.firmware})
                    except StorageError as e:
                        self._storage_failed(e)
                self._emit("state", f"Reconnected after {attempt} attempt(s)")
                await self._apply_streams()
                await self._update_battery()
                self._battery_task = asyncio.create_task(self._battery_loop())
                return
            self._deadline = None
            if self.writer:
                self._finish_log("complete", "connection_timeout")
            await self.transport.disconnect()
            self._refresh(state=State.IDLE, reconnect_attempt=0)
            self._emit("error", "Reconnect grace period expired; disconnected")
        finally:
            self._reconnect_task = None
            self._retry_signal = None

    async def _wait_before_retry(self, delay: float) -> None:
        """Sleep, but wake early when the user asks for an immediate retry."""
        sig = self._retry_signal
        if sig is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(sig.wait(), delay)
        except asyncio.TimeoutError:
            return
        sig.clear()

    def retry_now(self) -> None:
        """Stop waiting and try to reconnect straight away (call on the loop thread)."""
        if self._retry_signal is not None:
            self._retry_signal.set()

    def extend_grace(self, seconds: float) -> None:
        """Give the strap more time to come back (call on the loop thread)."""
        if self._deadline is None:
            return
        self._deadline += seconds
        self._emit("info", f"Reconnect grace extended by {int(seconds)} s")

    # --- logging ---------------------------------------------------------------------------
    def start_log(self, opts: LogOptions) -> Path:
        if self.state != State.CONNECTED:
            raise RuntimeError("Connect to the H10 before starting a log")
        if self.writer is not None:
            raise RuntimeError("A log is already running")
        dev, det = self.snap.device, self.snap.details or DeviceDetails()
        device = {"name": dev.name if dev else None, "device_id": dev.device_id if dev else None,
                  "address": dev.address if dev else None, "model": det.model,
                  "firmware": det.firmware, "serial": det.serial}
        writer = SessionWriter(opts.output_root, opts.participant_id, device=device,
                               condition=opts.condition, notes=opts.notes,
                               grace_s=self.grace_s, clock=self.clock,
                               battery=self.snap.battery)
        self.writer = writer
        self._failure = None
        self._session_log = attach_session_log(writer.folder)
        for kind, (rate, rng) in self._active.items():
            self._stream_event(kind, _stream_meta(kind, rate, rng))
        self.metrics.reset_full()
        self.keep_awake.acquire()
        self._emit("log_started", f"Logging to {writer.folder}")
        return writer.folder

    def stop_log(self, end_reason: str = "user_stop") -> Path | None:
        if self.writer is None:
            return None
        return self._finish_log("complete", end_reason)

    def _finish_log(self, status: str, end_reason: str) -> Path | None:
        w, self.writer = self.writer, None
        if w is None:
            return None
        self._last_folder = w.folder
        try:
            w.finalize(status, end_reason, self.metrics.window(None),
                       self.metrics.full.hr_min, self.metrics.full.hr_max)
        except StorageError as e:
            self._failure = f"Storage failure: {e}. Data so far is in {w.folder}"
            self._emit("error", self._failure)
        finally:
            self.keep_awake.release()
            detach_session_log(self._session_log)
            self._session_log = None
        self._emit("log_stopped", f"Log saved ({end_reason}): {w.folder}")
        return w.folder

    def _storage_failed(self, err: StorageError) -> None:
        w, self.writer = self.writer, None
        if w is None:
            return
        self._last_folder = w.folder
        self._failure = f"Storage failure: {err}. Logging stopped; data so far is in {w.folder}"
        try:
            w.finalize("failed", "storage_failure", self.metrics.window(None),
                       self.metrics.full.hr_min, self.metrics.full.hr_max)
        except StorageError:
            w.close_quietly()
        finally:
            self.keep_awake.release()
            detach_session_log(self._session_log)
            self._session_log = None
        self._emit("error", self._failure)
        self._emit("log_stopped", f"Log ended (storage_failure): {w.folder}")

    # --- disconnect / shutdown -------------------------------------------------------------
    def _cancel_battery(self) -> None:
        if self._battery_task:
            self._battery_task.cancel()
            self._battery_task = None

    async def disconnect(self, end_reason: str = "user_stop") -> None:
        if self.writer:
            self._finish_log("complete", end_reason)
        task, self._reconnect_task = self._reconnect_task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._deadline = None
        self._cancel_battery()
        prev = self.state
        self._refresh(state=State.DISCONNECTING)
        for kind in list(self._active):
            await self.transport.stop_stream(kind)
        self._active.clear()
        await self.transport.disconnect()
        self._refresh(state=State.IDLE)
        if prev != State.IDLE:
            self._emit("state", "Disconnected")

    async def shutdown(self) -> None:
        await self.disconnect("window_close")
