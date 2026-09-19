"""Tkinter Live Recording window. BLE and file work run on a separate asyncio thread."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__
from .config import AppConfig, load_config, save_config
from .logsetup import setup_logging
from . import pmd
from .metrics import WINDOW_CHOICES
from .recover import recover_interrupted
from .session import Event, LogOptions, SessionController, Snapshot, State, StreamConfig
from .storage import StorageError, validate_participant
from .transport import BleTransport, BluetoothUnavailable, ConnectError, DeviceInfo, FakeTransport

log = logging.getLogger(__name__)
REFRESH_MS = 500  # live values redraw at most twice per second
POLL_MS = 100
CHART_SPAN_S = 300
ECG_SPAN_S = 6
ACC_SPAN_S = 20
GAP_S = 2.0
CLOSE_TIMEOUT_S = 20
NO_DEVICE_TIPS = ("No Polar H10 found.\n\n• Wear the strap and wet the electrodes.\n"
                  "• Move closer to the PC.\n• Close other apps or phones connected to the "
                  "strap (or enable dual Bluetooth in the Polar app).")

COLORS = {"hr": "#d62839", "rr": "#1f6feb", "grid": "#e3e3e3", "axis": "#6b6b6b",
          "ecg": "#0b7a3e", "x": "#d62839", "y": "#2e7d32", "z": "#1f6feb",
          "amber": "#f5b700", "red": "#c62828", "rec": "#d62839", "ok": "#2e7d32"}


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


class Chart(tk.Canvas):
    """Minimal scrolling line chart with auto-scaled y axis and optional several series."""

    def __init__(self, master, title: str, unit: str, scale: float = 1.0, *,
                 span_s: float = CHART_SPAN_S, tick_s: float = 60, min_pad: float = 5.0,
                 empty: str = "No data yet - Scan, then Connect", **kw) -> None:
        super().__init__(master, background="white", highlightthickness=1,
                         highlightbackground="#cccccc", **kw)
        self.title, self.unit, self.k = title, unit, scale
        self.span_s, self.tick_s, self.min_pad, self.empty = span_s, tick_s, min_pad, empty

    def _tick_label(self, secs: float) -> str:
        if secs == 0:
            return "now"
        return f"-{secs / 60:g}m" if self.tick_s >= 60 else f"-{secs:g}s"

    def draw(self, series: list[tuple[deque, str, str]], now_s: float,
             message: str | None = None) -> None:
        """series: [(points (t, value), color, legend label), ...]"""
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 40:
            return
        k = self.k
        left, right, top, bottom = int(50 * k), int(22 * k), int(24 * k), int(20 * k)
        pw, ph = w - left - right, h - top - bottom
        if pw < 20 or ph < 20:
            return
        self.create_text(left, int(4 * k), anchor="nw", text=f"{self.title} ({self.unit})",
                         fill=COLORS["axis"], font=("Segoe UI", 9, "bold"))
        x_leg = w - right
        for _, color, label in reversed(series):
            if label:
                t = self.create_text(x_leg, int(4 * k), anchor="ne", text=label, fill=color,
                                     font=("Segoe UI", 9, "bold"))
                x_leg = self.bbox(t)[0] - int(10 * k)
        t0 = now_s - self.span_s
        vis = [[(t, v) for t, v in pts if t >= t0] for pts, _, _ in series]
        values = [v for pts in vis for _, v in pts]
        if message or not values:
            self.create_text(w / 2, h / 2, text=message or self.empty, fill=COLORS["axis"],
                             font=("Segoe UI", 10))
            return
        lo, hi = min(values), max(values)
        pad = max((hi - lo) * 0.15, self.min_pad)
        lo, hi = lo - pad, hi + pad
        for i in range(5):  # horizontal grid
            v = lo + (hi - lo) * i / 4
            y = top + ph - ph * i / 4
            self.create_line(left, y, left + pw, y, fill=COLORS["grid"])
            self.create_text(left - 4, y, anchor="e", text=f"{v:.0f}", fill=COLORS["axis"],
                             font=("Segoe UI", 8))
        n_ticks = int(self.span_s // self.tick_s)
        for m in range(n_ticks + 1):  # time ticks
            x = left + pw * (1 - m * self.tick_s / self.span_s)
            self.create_line(x, top, x, top + ph, fill=COLORS["grid"])
            self.create_text(x, top + ph + 2, anchor="n", text=self._tick_label(m * self.tick_s),
                             fill=COLORS["axis"], font=("Segoe UI", 8))
        width = max(1, round(1.5 * k))
        max_pts = max(200, pw * 2)
        for pts, (_, color, _) in zip(vis, series):
            if len(pts) > max_pts:  # thin for drawing only; stored data is untouched
                step = len(pts) / max_pts
                pts = [pts[int(i * step)] for i in range(max_pts)]
            seg: list[float] = []
            prev_t = None
            for t, v in pts:
                x = left + pw * (t - t0) / self.span_s
                y = top + ph - ph * (v - lo) / (hi - lo)
                if prev_t is not None and t - prev_t > GAP_S:
                    if len(seg) >= 4:
                        self.create_line(*seg, fill=color, width=width)
                    seg = []
                seg += [x, y]
                prev_t = t
            if len(seg) >= 4:
                self.create_line(*seg, fill=color, width=width)
            elif len(seg) == 2:
                self.create_oval(seg[0] - 2, seg[1] - 2, seg[0] + 2, seg[1] + 2, fill=color,
                                 outline="")


class App:
    def __init__(self, root: tk.Tk, fake: bool = False) -> None:
        self.root = root
        self.cfg: AppConfig = load_config()
        self.events: queue.Queue[Event] = queue.Queue()
        self.runner = AsyncRunner()
        transport = FakeTransport() if fake else BleTransport()
        self.ctrl = SessionController(transport, self.events.put, grace_s=self.cfg.grace_s)
        self.devices: list[DeviceInfo] = []
        self.snap = Snapshot()
        self.hr_pts: deque[tuple[float, float]] = deque()
        self.rr_pts: deque[tuple[float, float]] = deque()
        self.ecg_pts: deque[tuple[float, float]] = deque()
        self.acc_pts = {axis: deque() for axis in "xyz"}
        self.ecg_now = self.acc_now = 0.0
        self.ctrl.stream_config = StreamConfig(self.cfg.ecg, self.cfg.acc, self.cfg.acc_rate_hz,
                                               self.cfg.acc_range_g)
        self.now_s = 0.0
        self.dirty = True
        self.busy = False
        self.closing = False
        self._build()
        self._apply_state()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(POLL_MS, self._poll)
        root.after(REFRESH_MS, self._refresh_loop)
        threading.Thread(target=self._startup_recovery, daemon=True).start()

    # --- layout ----------------------------------------------------------------------------
    def _build(self) -> None:
        r = self.root
        r.title(f"{APP_NAME} {__version__}")
        k = self.k = float(r.tk.call("tk", "scaling")) / (96 / 72)
        w = min(int(1060 * k), int(r.winfo_screenwidth() * 0.95))
        h = min(int(860 * k), int(r.winfo_screenheight() * 0.9))
        r.geometry(f"{w}x{h}")
        r.minsize(min(w, int(900 * k)), min(h, int(700 * k)))
        style = ttk.Style(r)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Big.TLabel", font=("Segoe UI", 60, "bold"), foreground=COLORS["hr"])
        style.configure("Unit.TLabel", font=("Segoe UI", 14))
        style.configure("Head.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Rec.TLabel", font=("Segoe UI", 11, "bold"), foreground=COLORS["rec"])
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

        outer = ttk.Frame(r, padding=10)
        outer.pack(fill="both", expand=True)

        # device row
        dev = ttk.LabelFrame(outer, text="1. Device", padding=8)
        dev.pack(fill="x")
        self.btn_scan = ttk.Button(dev, text="Scan", command=self.on_scan)
        self.btn_scan.pack(side="left")
        self.device_var = tk.StringVar()
        self.cmb_device = ttk.Combobox(dev, textvariable=self.device_var, state="readonly",
                                       width=34)
        self.cmb_device.pack(side="left", padx=6)
        self.btn_connect = ttk.Button(dev, text="Connect", style="Accent.TButton",
                                      command=self.on_connect)
        self.btn_connect.pack(side="left")
        self.btn_disconnect = ttk.Button(dev, text="Disconnect", command=self.on_disconnect)
        self.btn_disconnect.pack(side="left", padx=6)
        self.state_lbl = ttk.Label(dev, text="Not connected", style="Head.TLabel")
        self.state_lbl.pack(side="left", padx=12)
        ttk.Button(dev, text="About", command=self.on_about).pack(side="right")

        st = ttk.Frame(outer)
        st.pack(fill="x", pady=(6, 0))
        ttk.Label(st, text="Extra sensor streams:", style="Head.TLabel").pack(side="left")
        self.ecg_var = tk.BooleanVar(value=self.cfg.ecg)
        self.acc_var = tk.BooleanVar(value=self.cfg.acc)
        self.acc_rate_var = tk.StringVar(value=str(self.cfg.acc_rate_hz))
        self.acc_range_var = tk.StringVar(value=f"±{self.cfg.acc_range_g} g")
        ttk.Checkbutton(st, text="ECG (130 Hz)", variable=self.ecg_var,
                        command=self.on_streams).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(st, text="Accelerometer", variable=self.acc_var,
                        command=self.on_streams).pack(side="left", padx=(16, 4))
        ttk.Label(st, text="rate").pack(side="left")
        cmb_rate = ttk.Combobox(st, textvariable=self.acc_rate_var, state="readonly", width=5,
                                values=[str(r) for r in pmd.ACC_RATES_HZ])
        cmb_rate.pack(side="left", padx=(4, 2))
        ttk.Label(st, text="Hz   range").pack(side="left")
        cmb_range = ttk.Combobox(st, textvariable=self.acc_range_var, state="readonly", width=6,
                                 values=[f"±{g} g" for g in pmd.ACC_RANGES_G])
        cmb_range.pack(side="left", padx=4)
        for cmb in (cmb_rate, cmb_range):
            cmb.bind("<<ComboboxSelected>>", lambda e: self.on_streams())
        self.streams_lbl = ttk.Label(st, text="", foreground=COLORS["axis"])
        self.streams_lbl.pack(side="left", padx=12)

        self.banner = tk.Label(outer, text="", anchor="w", padx=8, pady=4,
                               font=("Segoe UI", 10, "bold"))

        # live panel
        live = ttk.LabelFrame(outer, text="Live", padding=8)
        live.pack(fill="both", expand=True, pady=(8, 0))
        self.live_frame = live
        left = ttk.Frame(live)
        left.pack(side="left", fill="y")
        hr_row = ttk.Frame(left)
        hr_row.pack(anchor="w")
        self.hr_lbl = ttk.Label(hr_row, text="--", style="Big.TLabel")
        self.hr_lbl.pack(side="left")
        ttk.Label(hr_row, text="bpm", style="Unit.TLabel").pack(side="left", anchor="s",
                                                                 pady=(0, 18))
        self.rr_lbl = ttk.Label(left, text="Last RR: -- ms")
        self.rr_lbl.pack(anchor="w")
        self.bat_lbl = ttk.Label(left, text="Battery: --")
        self.bat_lbl.pack(anchor="w", pady=(6, 0))
        self.contact_lbl = ttk.Label(left, text="Contact: --")
        self.contact_lbl.pack(anchor="w")
        self.devinfo_lbl = ttk.Label(left, text="", foreground=COLORS["axis"])
        self.devinfo_lbl.pack(anchor="w", pady=(6, 0))

        self.tabs = ttk.Notebook(live)
        self.tabs.pack(side="left", fill="both", expand=True, padx=(12, 0))
        charts = ttk.Frame(self.tabs, padding=4)
        self.tabs.add(charts, text="  HR & RR  ")
        self.hr_chart = Chart(charts, "Heart rate", "bpm", k, height=int(140 * k))
        self.hr_chart.pack(fill="both", expand=True)
        self.rr_chart = Chart(charts, "RR interval", "ms", k, min_pad=20.0, height=int(140 * k))
        self.rr_chart.pack(fill="both", expand=True, pady=(6, 0))
        ecg_tab = ttk.Frame(self.tabs, padding=4)
        self.tabs.add(ecg_tab, text="  ECG  ")
        self.ecg_chart = Chart(ecg_tab, "ECG", "µV", k, span_s=ECG_SPAN_S, tick_s=1,
                               min_pad=100.0, height=int(280 * k))
        self.ecg_chart.pack(fill="both", expand=True)
        acc_tab = ttk.Frame(self.tabs, padding=4)
        self.tabs.add(acc_tab, text="  Accelerometer  ")
        self.acc_chart = Chart(acc_tab, "Acceleration", "mG", k, span_s=ACC_SPAN_S, tick_s=5,
                               min_pad=50.0, height=int(280 * k))
        self.acc_chart.pack(fill="both", expand=True)
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: setattr(self, "dirty", True))

        met = ttk.Frame(outer)
        met.pack(fill="x", pady=(6, 0))
        win_row = met
        ttk.Label(win_row, text="Metric window", style="Head.TLabel").pack(side="left")
        self.window_var = tk.StringVar(value=self.cfg.metric_window
                                       if self.cfg.metric_window in WINDOW_CHOICES else "60 s")
        cmb_win = ttk.Combobox(win_row, textvariable=self.window_var, state="readonly",
                               values=list(WINDOW_CHOICES), width=12)
        cmb_win.pack(side="left", padx=(6, 12))
        cmb_win.bind("<<ComboboxSelected>>", lambda e: self.on_window())
        self.metric_lbls: dict[str, ttk.Label] = {}
        for key, name in [("hr_mean", "Mean HR"), ("rr_mean", "Mean RR"), ("sdnn", "SDNN"),
                          ("rmssd", "RMSSD"), ("pnn50", "pNN50")]:
            ttk.Label(win_row, text=name).pack(side="left", padx=(10, 4))
            lbl = ttk.Label(win_row, text="--", style="Head.TLabel", width=9)
            lbl.pack(side="left")
            self.metric_lbls[key] = lbl
        ttk.Label(win_row, text="descriptive, not medical",
                  foreground=COLORS["axis"]).pack(side="right")

        # logging panel
        lg = ttk.LabelFrame(outer, text="2. Log to CSV (optional)", padding=8)
        lg.pack(fill="x", pady=(8, 0))
        form = ttk.Frame(lg)
        form.pack(fill="x")
        self.pid_var = tk.StringVar()
        self.cond_var = tk.StringVar(value=self.cfg.condition)
        self.notes_var = tk.StringVar()
        self.out_var = tk.StringVar(value=self.cfg.output_root)
        self.grace_var = tk.StringVar(value=str(self.cfg.grace_s))
        ttk.Label(form, text="Participant ID *").grid(row=0, column=0, sticky="w")
        self.ent_pid = ttk.Entry(form, textvariable=self.pid_var, width=18)
        self.ent_pid.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Condition").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(form, textvariable=self.cond_var, width=16).grid(row=0, column=3, sticky="w",
                                                                  padx=4)
        ttk.Label(form, text="Reconnect grace (s)").grid(row=0, column=4, sticky="w",
                                                         padx=(12, 0))
        ttk.Spinbox(form, textvariable=self.grace_var, from_=0, to=86400, increment=30,
                    width=7).grid(row=0, column=5, sticky="w", padx=4)
        ttk.Label(form, text="Notes").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.notes_var).grid(row=1, column=1, columnspan=5,
                                                          sticky="we", padx=4, pady=(6, 0))
        ttk.Label(form, text="Output folder").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.out_var).grid(row=2, column=1, columnspan=4,
                                                        sticky="we", padx=4, pady=(6, 0))
        ttk.Button(form, text="Browse", command=self.on_browse).grid(row=2, column=5,
                                                                     sticky="w", pady=(6, 0))
        form.columnconfigure(3, weight=1)
        ttk.Label(lg, text="Use a research code, not a name. Notes are stored in session.json; "
                           "avoid personal details.", foreground=COLORS["axis"]).pack(anchor="w")

        btns = ttk.Frame(lg)
        btns.pack(fill="x", pady=(8, 0))
        self.btn_start = ttk.Button(btns, text="Start logging", style="Accent.TButton",
                                    command=self.on_start_log)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="Stop logging", command=self.on_stop_log)
        self.btn_stop.pack(side="left", padx=6)
        self.btn_open = ttk.Button(btns, text="Open session folder", command=self.on_open_folder)
        self.btn_open.pack(side="left")
        self.rec_lbl = ttk.Label(btns, text="", style="Rec.TLabel")
        self.rec_lbl.pack(side="left", padx=12)
        self.sess_lbl = ttk.Label(lg, text="", foreground=COLORS["axis"])
        self.sess_lbl.pack(anchor="w", pady=(6, 0))

        # events
        ev = ttk.LabelFrame(outer, text="Recent events", padding=(8, 4))
        ev.pack(fill="x", pady=(8, 0))
        self.ev_list = tk.Listbox(ev, height=4, activestyle="none", borderwidth=0,
                                  font=("Consolas", 9))
        self.ev_list.pack(fill="x")

    # --- helpers ---------------------------------------------------------------------------
    def _add_event(self, kind: str, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.ev_list.insert(0, f"{stamp}  {kind:<8} {text}")
        if kind == "error":
            self.ev_list.itemconfig(0, foreground=COLORS["red"])
        elif kind == "warning":
            self.ev_list.itemconfig(0, foreground="#8a6d00")
        while self.ev_list.size() > 50:
            self.ev_list.delete("end")

    def _selected_device(self) -> DeviceInfo | None:
        i = self.cmb_device.current()
        return self.devices[i] if 0 <= i < len(self.devices) else None

    def _save_cfg(self) -> None:
        dev = self._selected_device()
        if dev:
            self.cfg.last_device_id = dev.device_id
        self.cfg.output_root = self.out_var.get().strip() or self.cfg.output_root
        self.cfg.condition = self.cond_var.get().strip()
        self.cfg.metric_window = self.window_var.get()
        cfg = self._stream_config()
        self.cfg.ecg, self.cfg.acc = cfg.ecg, cfg.acc
        self.cfg.acc_rate_hz, self.cfg.acc_range_g = cfg.acc_rate_hz, cfg.acc_range_g
        try:
            self.cfg.grace_s = max(0, min(86400, int(self.grace_var.get())))
        except ValueError:
            pass
        save_config(self.cfg)

    def _run(self, coro, on_done=None, on_error=None) -> None:
        """Run a coroutine on the BLE thread; callbacks run on the Tk thread."""
        self.busy = True
        self._apply_state()
        fut = self.runner.submit(coro)

        def done(f: concurrent.futures.Future) -> None:
            def ui() -> None:
                self.busy = False
                exc = f.exception()
                if exc is not None:
                    if on_error:
                        on_error(exc)
                    else:
                        log.error("background task failed: %r", exc)
                        self._add_event("error", str(exc) or type(exc).__name__)
                elif on_done:
                    on_done(f.result())
                self._apply_state()
            if not self.closing:
                self.root.after(0, ui)
        fut.add_done_callback(done)

    def _apply_state(self) -> None:
        s = self.snap
        st = s.state
        idle = st == State.IDLE
        connected = st == State.CONNECTED
        can = lambda ok: "!disabled" if ok and not self.busy else "disabled"  # noqa: E731
        self.btn_scan.state([can(idle)])
        self.cmb_device.state([can(idle and bool(self.devices))])
        if not (idle and bool(self.devices)):
            self.cmb_device.state(["disabled"])
        else:
            self.cmb_device.state(["!disabled", "readonly"])
        self.btn_connect.state([can(idle and self._selected_device() is not None)])
        self.btn_disconnect.state([can(not idle and st != State.DISCONNECTING)])
        self.btn_start.state([can(connected and not s.logging)])
        self.btn_stop.state([can(s.logging)])
        folder = s.session_folder or s.last_session_folder
        self.btn_open.state(["!disabled" if folder else "disabled"])
        text = {State.IDLE: "Not connected", State.CONNECTING: "Connecting...",
                State.CONNECTED: "Connected", State.RECONNECTING: "Reconnecting...",
                State.DISCONNECTING: "Disconnecting..."}[st]
        if s.device and st != State.IDLE:
            text += f" - {s.device.name}"
        self.state_lbl.configure(text=text, foreground=COLORS["ok"] if connected else "")

    # --- event pump ------------------------------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                e = self.events.get_nowait()
                self.snap = e.snapshot
                if e.kind == "pmd":
                    self._add_pmd(e)
                elif e.kind == "packet":
                    t = e.t_s or 0.0
                    self.now_s = t
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
                    cutoff = t - CHART_SPAN_S - 10
                    for d in (self.hr_pts, self.rr_pts):
                        while d and d[0][0] < cutoff:
                            d.popleft()
                else:
                    if e.kind == "state" and e.snapshot.state == State.CONNECTED and \
                            "Connected to" in e.detail:
                        self.hr_pts.clear()
                        self.rr_pts.clear()
                        self.ecg_pts.clear()
                        for d in self.acc_pts.values():
                            d.clear()
                        self.now_s = self.ecg_now = self.acc_now = 0.0
                    if e.detail:
                        self._add_event(e.kind, e.detail)
                    self._apply_state()
                self.dirty = True
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(POLL_MS, self._poll)

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

    def _refresh_loop(self) -> None:
        if self.dirty:
            self.dirty = False
            self._render()
        if not self.closing:
            self.root.after(REFRESH_MS, self._refresh_loop)

    def _render(self) -> None:
        s = self.snap
        w = s.window
        live = s.state in (State.CONNECTED, State.RECONNECTING)
        self.hr_lbl.configure(text=str(w.latest_hr) if live and w and w.latest_hr else "--")
        last_rr = getattr(self, "last_rr", None)
        self.rr_lbl.configure(text=f"Last RR: {last_rr:.0f} ms" if live and last_rr
                              else "Last RR: -- ms")
        self.bat_lbl.configure(text=f"Battery: {s.battery}%" if s.battery is not None
                               else "Battery: --")
        contact = {True: "yes", False: "no", None: "not reported"}[s.contact]
        self.contact_lbl.configure(text=f"Contact: {contact if live else '--'}")
        if s.details and s.device:
            self.devinfo_lbl.configure(text=f"ID {s.device.device_id} · model "
                                            f"{s.details.model or '?'} · fw "
                                            f"{s.details.firmware or '?'}")
        fmt = {"hr_mean": ("{:.1f} bpm", "hr_mean"), "rr_mean": ("{:.0f} ms", "rr_mean"),
               "sdnn": ("{:.1f} ms", "sdnn"), "rmssd": ("{:.1f} ms", "rmssd"),
               "pnn50": ("{:.1f} %", "pnn50")}
        for key, (f, attr) in fmt.items():
            v = getattr(w, attr) if w else None
            self.metric_lbls[key].configure(text=f.format(v) if v is not None else "--")
        if s.logging:
            el = int(s.elapsed_s)
            pct = f"{s.connected_pct:.1f}%" if s.connected_pct is not None else "--"
            self.rec_lbl.configure(text=f"● REC  {el // 3600:02d}:{el % 3600 // 60:02d}:"
                                        f"{el % 60:02d}")
            self.sess_lbl.configure(
                text=f"Packets {s.packets}   RR {s.rr}   Excluded RR {s.rr_excluded}   "
                     f"Disconnects {s.disconnects}   Connected {pct}   HR min/max "
                     f"{s.hr_min or '--'}/{s.hr_max or '--'}"
                     + (f"   ECG {s.ecg_samples}" if s.ecg_samples else "")
                     + (f"   ACC {s.acc_samples}" if s.acc_samples else "")
                     + f"   →  {s.session_folder}")
        else:
            self.rec_lbl.configure(text="")
            self.sess_lbl.configure(text=f"Last log: {s.last_session_folder}"
                                    if s.last_session_folder else "Not logging.")
        # banner
        if s.failure:
            self.banner.configure(text=s.failure, background=COLORS["red"], foreground="white")
            self.banner.pack(fill="x", pady=(8, 0), before=self.live_frame)
        elif s.state == State.RECONNECTING:
            left = s.reconnect_left_s or 0
            self.banner.configure(text=f"Connection lost - reconnecting, {int(left) // 60}:"
                                       f"{int(left) % 60:02d} left"
                                       + (" (log continues in the same folder)"
                                          if s.logging else ""),
                                  background=COLORS["amber"], foreground="black")
            self.banner.pack(fill="x", pady=(8, 0), before=self.live_frame)
            self.dirty = True  # keep countdown ticking
        else:
            self.banner.pack_forget()
        tab = self.tabs.index(self.tabs.select())
        if tab == 0:
            self.hr_chart.draw([(self.hr_pts, COLORS["hr"], "")], self.now_s)
            self.rr_chart.draw([(self.rr_pts, COLORS["rr"], "")], self.now_s)
        elif tab == 1:
            self.ecg_chart.draw([(self.ecg_pts, COLORS["ecg"], "")], self.ecg_now,
                                self._stream_message(pmd.ECG, "ECG"))
        else:
            self.acc_chart.draw([(self.acc_pts[a], COLORS[a], a) for a in "xyz"], self.acc_now,
                                self._stream_message(pmd.ACC, "Accelerometer"))
        on = [pmd.STREAM_NAMES[k] for k in sorted(s.streams_active)]
        self.streams_lbl.configure(text=("Streaming: " + ", ".join(on)) if on else "")

    # --- actions ---------------------------------------------------------------------------
    def on_scan(self) -> None:
        self._add_event("info", "Scanning for Polar H10 (8 s)...")

        def done(devs: list[DeviceInfo]) -> None:
            self.devices = devs
            self.cmb_device.configure(values=[d.label for d in devs])
            if not devs:
                self.cmb_device.set("")
                messagebox.showinfo(APP_NAME, NO_DEVICE_TIPS, parent=self.root)
                return
            idx = next((i for i, d in enumerate(devs)
                        if d.device_id == self.cfg.last_device_id), 0)
            self.cmb_device.current(idx)
            self._add_event("info", f"Found {len(devs)} device(s)")

        def err(e: BaseException) -> None:
            msg = (f"Bluetooth is unavailable: {e}\n\nTurn Bluetooth on in Windows settings."
                   if isinstance(e, BluetoothUnavailable) else f"Scan failed: {e}")
            self._add_event("error", msg.splitlines()[0])
            messagebox.showerror(APP_NAME, msg, parent=self.root)
        self._run(self.ctrl.scan(), done, err)

    def on_connect(self) -> None:
        dev = self._selected_device()
        if dev is None:
            return
        self._save_cfg()
        self.ctrl.grace_s = self.cfg.grace_s

        def err(e: BaseException) -> None:
            if not isinstance(e, ConnectError):
                self._add_event("error", f"Connect failed: {e}")
            messagebox.showerror(APP_NAME, f"Could not connect to {dev.name}.\n\n{e}\n\n"
                                 + NO_DEVICE_TIPS.split("\n\n", 1)[1], parent=self.root)
        self._run(self.ctrl.connect(dev), on_error=err)

    def on_disconnect(self) -> None:
        if self.snap.logging and not messagebox.askyesno(
                APP_NAME, "A log is running. Stop logging and disconnect?", parent=self.root):
            return
        self._run(self.ctrl.disconnect())

    def _stream_config(self) -> StreamConfig:
        try:
            rate = int(self.acc_rate_var.get())
        except ValueError:
            rate = 50
        rng = int(re.sub(r"\D", "", self.acc_range_var.get()) or 8)
        return StreamConfig(self.ecg_var.get(), self.acc_var.get(), rate, rng)

    def _stream_message(self, kind: int, name: str) -> str | None:
        s = self.snap
        if s.state not in (State.CONNECTED, State.RECONNECTING):
            return None
        if kind not in s.streams_active:
            if s.streams_available and kind not in s.streams_available:
                return f"{name} is not available on this device"
            return f"{name} is off - tick it in 'Extra sensor streams' above"
        return None

    def on_streams(self) -> None:
        cfg = self._stream_config()
        self._save_cfg()
        fut = self.runner.submit(self.ctrl.set_streams(cfg))

        def done(f: concurrent.futures.Future) -> None:
            if f.exception() and not self.closing:
                self.root.after(0, lambda: self._add_event("error", str(f.exception())))
        fut.add_done_callback(done)
        self.dirty = True

    def on_window(self) -> None:
        self.runner.call(self.ctrl.set_window, self.window_var.get())
        self._save_cfg()

    def on_browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()),
                                    parent=self.root)
        if d:
            self.out_var.set(str(Path(d)))

    def on_start_log(self) -> None:
        try:
            pid = validate_participant(self.pid_var.get())
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e), parent=self.root)
            self.ent_pid.focus_set()
            return
        try:
            grace = int(self.grace_var.get())
            if not 0 <= grace <= 86400:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "Reconnect grace must be 0-86400 seconds.",
                                   parent=self.root)
            return
        self._save_cfg()
        opts = LogOptions(pid, Path(self.out_var.get().strip()), self.cond_var.get().strip(),
                          self.notes_var.get().strip())

        def start():
            self.ctrl.grace_s = grace
            return self.ctrl.start_log(opts)

        def err(e: BaseException) -> None:
            msg = str(e)
            self._add_event("error", f"Could not start logging: {msg}")
            messagebox.showerror(APP_NAME, f"Could not start logging.\n\n{msg}",
                                 parent=self.root)

        async def start_coro():
            return start()
        self._run(start_coro(), on_error=err)

    def on_stop_log(self) -> None:
        async def stop():
            return self.ctrl.stop_log()
        self._run(stop())

    def on_open_folder(self) -> None:
        folder = self.snap.session_folder or self.snap.last_session_folder
        if folder and folder.exists():
            if sys.platform == "win32":
                os.startfile(folder)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(folder)])

    def on_about(self) -> None:
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {__version__}\n\nRecords Polar H10 heart rate and every RR interval "
            "to CSV using the standard Bluetooth Heart Rate service.\n\nOpen source, MIT "
            "license.\n\nPolar and H10 are trademarks of Polar Electro Oy. This software is "
            "independent and not affiliated with or endorsed by Polar Electro.\n\nValues are "
            "descriptive and not a medical or diagnostic measurement.", parent=self.root)

    def _startup_recovery(self) -> None:
        try:
            done = recover_interrupted(Path(self.cfg.output_root))
        except Exception as e:  # noqa: BLE001
            log.warning("startup recovery failed: %s", e)
            return
        for folder in done:
            self.events.put(Event("warning", f"Recovered interrupted session {folder.name}",
                                  self.snap))

    def on_close(self) -> None:
        if self.closing:
            return
        if self.snap.state == State.IDLE:
            self._save_cfg()
            self._destroy()
            return
        self._save_cfg()
        self.closing = True
        top = tk.Toplevel(self.root)
        top.title(APP_NAME)
        top.transient(self.root)
        ttk.Label(top, text="Stopping and saving...", padding=24,
                  font=("Segoe UI", 11)).pack()
        top.grab_set()
        fut = self.runner.submit(self.ctrl.shutdown())
        deadline = time.monotonic() + CLOSE_TIMEOUT_S

        def check() -> None:
            if fut.done() or time.monotonic() > deadline:
                self._destroy()
            else:
                self.root.after(100, check)
        check()

    def _destroy(self) -> None:
        self.closing = True
        self.runner.loop.call_soon_threadsafe(self.runner.loop.stop)
        self.root.destroy()


def _dpi_aware() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    setup_logging()
    log.info("GUI start %s", __version__)
    _dpi_aware()
    root = tk.Tk()
    fake = "--fake" in sys.argv or os.environ.get("POLARH10_FAKE") == "1"
    App(root, fake=fake)
    root.mainloop()


if __name__ == "__main__":
    main()
