"""Command-line recorder: PolarH10Logging-cli.exe."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .config import load_config
from .logsetup import setup_logging
from .recover import recover_session
from .session import Event, LogOptions, SessionController
from .storage import StorageError, validate_participant
from .transport import BleTransport, BluetoothUnavailable, ConnectError, FakeTransport

EXIT_OK, EXIT_BT, EXIT_NO_DEVICE, EXIT_INPUT, EXIT_STORAGE, EXIT_RECOVER = 0, 2, 3, 4, 5, 6
STATUS_EVERY_S = 10.0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="PolarH10Logging-cli",
                                description=f"{APP_NAME} {__version__}: record Polar H10 HR/RR "
                                            "to CSV.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan", action="store_true", help="list nearby Polar H10 straps")
    mode.add_argument("--record", action="store_true", help="record a session")
    mode.add_argument("--recover", metavar="SESSION_FOLDER", type=Path,
                      help="finish an interrupted session folder")
    p.add_argument("--participant", help="participant ID (letters, digits, _ or -)")
    p.add_argument("--device", help="device ID, e.g. 1C3E0231 (default: strongest signal)")
    p.add_argument("--out", type=Path, help="output folder")
    p.add_argument("--condition", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--duration", type=float, metavar="MIN", help="stop after MIN minutes")
    p.add_argument("--grace", type=int, metavar="SEC", help="reconnect grace period, seconds")
    p.add_argument("--fake", action="store_true", help="use a simulated H10")
    p.add_argument("--replay", type=Path, help="with --fake: replay a raw.jsonl file")
    p.add_argument("--replay-speed", type=float, default=1.0)
    p.add_argument("--version", action="version", version=__version__)
    return p


def _status_line(c: SessionController) -> str:
    s = c.snap
    w = s.window
    hr = w.latest_hr if w else None
    rmssd = f"{w.rmssd:.1f}" if w and w.rmssd is not None else "-"
    extra = f" reconnecting {s.reconnect_left_s:.0f}s left" if s.reconnect_left_s else ""
    return (f"[{s.elapsed_s / 60:6.1f} min] {s.state.value}{extra}  HR {hr or '-'} bpm  "
            f"packets {s.packets}  RR {s.rr}  RMSSD(60s) {rmssd} ms  battery "
            f"{s.battery if s.battery is not None else '-'}%")


async def _record(args: argparse.Namespace) -> int:
    cfg = load_config()
    try:
        pid = validate_participant(args.participant or "")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT
    out = args.out or Path(cfg.output_root)
    grace = cfg.grace_s if args.grace is None else max(0, min(86400, args.grace))
    transport = (FakeTransport(replay=args.replay, replay_speed=args.replay_speed)
                 if args.fake else BleTransport())

    def on_event(e: Event) -> None:
        if e.kind in ("warning", "error", "log_started", "log_stopped") or \
                (e.kind == "state" and e.detail):
            print(f"{e.kind}: {e.detail}", flush=True)

    c = SessionController(transport, on_event, grace_s=grace)
    print("Scanning...", flush=True)
    try:
        devices = await c.scan()
    except BluetoothUnavailable as e:
        print(f"error: Bluetooth unavailable: {e}", file=sys.stderr)
        return EXIT_BT
    if args.device:
        devices = [d for d in devices if d.device_id.upper() == args.device.upper()]
    if not devices:
        print("error: no Polar H10 found. Wear and wet the strap, move closer, and close other "
              "apps connected to it.", file=sys.stderr)
        return EXIT_NO_DEVICE
    try:
        await c.connect(devices[0])
    except ConnectError:
        return EXIT_NO_DEVICE
    try:
        c.start_log(LogOptions(pid, out, args.condition, args.notes))
    except (StorageError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        await c.disconnect()
        return EXIT_INPUT

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, stop.set)
    except NotImplementedError:  # Windows: KeyboardInterrupt handled in main()
        pass
    deadline = loop.time() + args.duration * 60 if args.duration else None
    reason = "user_stop"
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), STATUS_EVERY_S)
            except asyncio.TimeoutError:
                pass
            if c.writer is None:  # timed out, storage failure, or grace expired
                break
            print(_status_line(c), flush=True)
            if deadline and loop.time() >= deadline:
                reason = "duration_elapsed"
                break
    finally:
        failed = c.snap.failure is not None
        await c.disconnect(reason)
    return EXIT_STORAGE if failed else EXIT_OK


async def _scan(args: argparse.Namespace) -> int:
    t = FakeTransport() if args.fake else BleTransport()
    try:
        devices = await t.scan()
    except BluetoothUnavailable as e:
        print(f"error: Bluetooth unavailable: {e}", file=sys.stderr)
        return EXIT_BT
    for d in devices:
        print(f"{d.device_id}\t{d.name}\t{d.address}\t{d.rssi} dBm")
    return EXIT_OK if devices else EXIT_NO_DEVICE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(console=False)
    logging.getLogger(__name__).info("CLI start %s", __version__)
    if args.recover:
        try:
            meta = recover_session(args.recover)
        except StorageError as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_RECOVER
        print(f"recovered {args.recover}: {meta['counts']['packets']} packets")
        return EXIT_OK
    try:
        return asyncio.run(_scan(args) if args.scan else _record(args))
    except KeyboardInterrupt:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
