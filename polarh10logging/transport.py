"""Bluetooth transport: real (bleak) and fake/replay for tests and demos."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import pmd
from .hr_parser import encode_hr_measurement

log = logging.getLogger(__name__)


def _uuid(short: str) -> str:
    return f"0000{short.lower()}-0000-1000-8000-00805f9b34fb"


HR_SERVICE_UUID = _uuid("180D")
HR_MEASUREMENT_UUID = _uuid("2A37")
BATTERY_LEVEL_UUID = _uuid("2A19")
MODEL_NUMBER_UUID = _uuid("2A24")
SERIAL_NUMBER_UUID = _uuid("2A25")
FIRMWARE_REVISION_UUID = _uuid("2A26")
DEVICE_NAME_PREFIX = "Polar H10"


class BluetoothUnavailable(Exception):
    """No usable Bluetooth adapter (off, absent, or blocked)."""


class ConnectError(Exception):
    """Connecting to or subscribing on the device failed."""


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    address: str
    device_id: str
    rssi: int | None = None

    @property
    def label(self) -> str:
        return f"{self.name}  ({self.rssi} dBm)" if self.rssi is not None else self.name


@dataclass(frozen=True)
class DeviceDetails:
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None


def device_id_from_name(name: str) -> str | None:
    if not name.startswith(DEVICE_NAME_PREFIX):
        return None
    rest = name[len(DEVICE_NAME_PREFIX):].strip()
    return rest or None


# on_packet(char_short, payload, ) ; on_disconnect()
PacketCallback = Callable[[str, bytes], None]
DisconnectCallback = Callable[[], None]


class Transport(Protocol):
    async def scan(self, timeout: float = 8.0) -> list[DeviceInfo]: ...
    async def connect(self, device: DeviceInfo, on_packet: PacketCallback,
                      on_disconnect: DisconnectCallback) -> DeviceDetails: ...
    async def read_battery(self) -> int | None: ...
    async def disconnect(self) -> None: ...
    def pmd_streams(self) -> set[int]: ...
    async def start_stream(self, kind: int, rate_hz: int = 0, range_g: int = 0) -> None: ...
    async def stop_stream(self, kind: int) -> None: ...


def _clean(b: bytes | bytearray) -> str:
    return bytes(b).decode("utf-8", "replace").strip("\x00 ") or ""


class BleTransport:
    """bleak-based transport. Keeps discovered BLEDevice objects for connecting."""

    def __init__(self) -> None:
        self._found: dict[str, object] = {}  # device_id -> BLEDevice
        self._client = None
        self._user_disconnect = False
        self._pmd: set[int] = set()
        self._cp_queue: asyncio.Queue[bytes] | None = None
        self._cp_lock = asyncio.Lock()
        self._link_tuned = False

    async def scan(self, timeout: float = 8.0) -> list[DeviceInfo]:
        from bleak import BleakScanner
        from bleak.exc import BleakError
        try:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        except (BleakError, OSError) as e:
            raise BluetoothUnavailable(str(e)) from e
        out = []
        for dev, adv in found.values():
            name = adv.local_name or dev.name or ""
            dev_id = device_id_from_name(name)
            if dev_id is None:
                continue
            self._found[dev_id] = dev
            out.append(DeviceInfo(name, dev.address, dev_id, adv.rssi))
        out.sort(key=lambda d: -(d.rssi if d.rssi is not None else -999))
        return out

    async def _find(self, device: DeviceInfo, rescan: bool):
        if not rescan and device.device_id in self._found:
            return self._found[device.device_id]
        from bleak import BleakScanner

        def match(d, adv) -> bool:
            return device_id_from_name(adv.local_name or d.name or "") == device.device_id
        dev = await BleakScanner.find_device_by_filter(match, timeout=8.0)
        if dev is None:
            raise ConnectError(f"{device.name} not found")
        self._found[device.device_id] = dev
        return dev

    async def connect(self, device: DeviceInfo, on_packet: PacketCallback,
                      on_disconnect: DisconnectCallback, rescan: bool = False) -> DeviceDetails:
        from bleak import BleakClient
        from bleak.exc import BleakError
        await self.disconnect()
        self._user_disconnect = False
        try:
            ble_dev = await self._find(device, rescan)

            def _lost(_client) -> None:
                if not self._user_disconnect:
                    on_disconnect()
            client = BleakClient(ble_dev, disconnected_callback=_lost, timeout=15.0)
            await client.connect()
            self._client = client
            details = DeviceDetails(
                model=await self._read_str(MODEL_NUMBER_UUID),
                firmware=await self._read_str(FIRMWARE_REVISION_UUID),
                serial=await self._read_str(SERIAL_NUMBER_UUID),
            )
            await client.start_notify(HR_MEASUREMENT_UUID,
                                      lambda _c, data: on_packet("2A37", bytes(data)))
            await self._setup_pmd(on_packet)
            return details
        except ConnectError:
            raise
        except (BleakError, OSError, asyncio.TimeoutError) as e:
            await self.disconnect()
            raise ConnectError(str(e) or type(e).__name__) from e

    async def _setup_pmd(self, on_packet: PacketCallback) -> None:
        """Subscribe to Polar's PMD service; ECG/ACC are optional, so failures are logged."""
        self._pmd = set()
        client = self._client
        if client is None or client.services.get_service(pmd.PMD_SERVICE_UUID) is None:
            return
        try:
            features = bytes(await client.read_gatt_char(pmd.PMD_CONTROL_UUID))
            self._cp_queue = asyncio.Queue()
            q = self._cp_queue
            await client.start_notify(pmd.PMD_CONTROL_UUID, lambda _c, d: q.put_nowait(bytes(d)))
            await client.start_notify(pmd.PMD_DATA_UUID,
                                      lambda _c, d: on_packet("PMD", bytes(d)))
            self._pmd = pmd.supported_streams(features)
        except Exception as e:  # noqa: BLE001
            log.warning("PMD unavailable: %s", e)

    def pmd_streams(self) -> set[int]:
        return set(self._pmd)

    async def _pmd_command(self, payload: bytes) -> None:
        if self._client is None or self._cp_queue is None:
            raise pmd.PmdError("not connected")
        async with self._cp_lock:
            while not self._cp_queue.empty():
                self._cp_queue.get_nowait()
            try:
                await self._client.write_gatt_char(pmd.PMD_CONTROL_UUID, payload, response=True)
                resp = await asyncio.wait_for(self._cp_queue.get(), 5)
            except asyncio.TimeoutError as e:
                raise pmd.PmdError("no response from the sensor") from e
            except Exception as e:  # noqa: BLE001
                raise pmd.PmdError(str(e) or type(e).__name__) from e
        pmd.check_response(resp, payload[0], payload[1])

    def _tune_link(self) -> None:
        """Ask Windows for 'balanced' connection parameters (Windows 11+).

        Measured on an H10 (fw 5.0.0): the strap holds ECG/ACC data until a connection
        parameter update completes, which otherwise stalls for about 30 s after connecting.
        Requesting balanced parameters completes it at once and data flows within ~2 s.
        ThroughputOptimized and PowerOptimized both made the H10 drop the link.
        """
        if self._link_tuned or self._client is None:
            return
        self._link_tuned = True
        try:
            from winrt.windows.devices.bluetooth import BluetoothLEPreferredConnectionParameters
            device = self._client._backend._requester  # bleak WinRT BluetoothLEDevice
            result = device.request_preferred_connection_parameters(
                BluetoothLEPreferredConnectionParameters.balanced)
            log.info("requested balanced connection parameters: status %s", result.status)
        except Exception as e:  # noqa: BLE001 - older Windows or bleak internals changed
            log.info("connection parameter request unavailable: %s", e)

    async def start_stream(self, kind: int, rate_hz: int = 0, range_g: int = 0) -> None:
        self._tune_link()
        if kind not in self._pmd:
            raise pmd.PmdError(f"{pmd.STREAM_NAMES.get(kind, kind)} not supported by this device")
        cmd = (pmd.start_ecg_command() if kind == pmd.ECG
               else pmd.start_acc_command(rate_hz, range_g))
        try:
            await self._pmd_command(cmd)
        except pmd.PmdError as e:
            if "already in state" not in str(e):
                raise
            await self._pmd_command(pmd.stop_command(kind))  # restart with new settings
            await self._pmd_command(cmd)

    async def stop_stream(self, kind: int) -> None:
        try:
            await self._pmd_command(pmd.stop_command(kind))
        except pmd.PmdError as e:
            log.info("stop %s: %s", pmd.STREAM_NAMES.get(kind, kind), e)

    async def _read_str(self, uuid: str) -> str | None:
        try:
            return _clean(await self._client.read_gatt_char(uuid)) or None
        except Exception:  # noqa: BLE001 - optional metadata
            return None

    async def read_battery(self) -> int | None:
        if self._client is None:
            return None
        try:
            return (await self._client.read_gatt_char(BATTERY_LEVEL_UUID))[0]
        except Exception:  # noqa: BLE001
            return None

    async def disconnect(self) -> None:
        self._pmd = set()
        self._link_tuned = False
        self._cp_queue = None
        client, self._client = self._client, None
        if client is None:
            return
        self._user_disconnect = True
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("disconnect: %s", e)


class FakeTransport:
    """Synthetic or replayed H10. `replay` is a raw.jsonl file written by this app."""

    def __init__(self, *, interval_s: float = 1.0, replay: Path | None = None,
                 replay_speed: float = 1.0, seed: int = 1, model: str = "H10",
                 device_id: str = "FAKE0001") -> None:
        self.interval_s = interval_s
        self.replay = replay
        self.replay_speed = replay_speed
        self.rng = random.Random(seed)
        self.model = model
        self.battery = 95
        self.device = DeviceInfo(f"{DEVICE_NAME_PREFIX} {device_id}", "00:00:00:00:00:00",
                                 device_id, -50)
        self.fail_connects = 0
        self.connect_calls = 0
        self.pmd_supported = {pmd.ECG, pmd.ACC}
        self.streams: dict[int, tuple[int, int]] = {}  # kind -> (rate, range)
        self.stream_starts = 0
        self._stream_tasks: dict[int, asyncio.Task] = {}
        self._sensor_ns = 1_000_000_000
        self._task: asyncio.Task | None = None
        self._on_disconnect: DisconnectCallback | None = None
        self._on_packet: PacketCallback | None = None

    async def scan(self, timeout: float = 8.0) -> list[DeviceInfo]:
        await asyncio.sleep(0)
        return [self.device]

    async def connect(self, device: DeviceInfo, on_packet: PacketCallback,
                      on_disconnect: DisconnectCallback, rescan: bool = False) -> DeviceDetails:
        self.connect_calls += 1
        await asyncio.sleep(0)
        if self.fail_connects > 0:
            self.fail_connects -= 1
            raise ConnectError("fake connect failure")
        self._on_packet, self._on_disconnect = on_packet, on_disconnect
        if self.interval_s > 0 or self.replay:
            self._task = asyncio.create_task(self._run())
        return DeviceDetails(self.model, "5.0.0", device.device_id)

    def inject(self, payload: bytes, char: str = "2A37") -> None:
        assert self._on_packet is not None
        self._on_packet(char, payload)

    def pmd_streams(self) -> set[int]:
        return set(self.pmd_supported)

    async def start_stream(self, kind: int, rate_hz: int = 0, range_g: int = 0) -> None:
        await asyncio.sleep(0)
        if kind not in self.pmd_supported or self._on_packet is None:
            raise pmd.PmdError(f"{pmd.STREAM_NAMES.get(kind, kind)} not supported")
        if kind == pmd.ACC:
            pmd.start_acc_command(rate_hz, range_g)  # validates settings
        self.stream_starts += 1
        self.streams[kind] = (rate_hz or pmd.ECG_RATE_HZ, range_g)
        if self.interval_s > 0 and kind not in self._stream_tasks:
            self._stream_tasks[kind] = asyncio.create_task(self._run_stream(kind))

    async def stop_stream(self, kind: int) -> None:
        self.streams.pop(kind, None)
        t = self._stream_tasks.pop(kind, None)
        if t:
            t.cancel()

    def emit_frame(self, kind: int, samples) -> None:
        """Send one PMD frame with the given samples (tests)."""
        rate = self.streams.get(kind, (pmd.ECG_RATE_HZ, 8))[0]
        self._sensor_ns += round(len(samples) * 1e9 / rate)
        if self._on_packet:
            self._on_packet("PMD", pmd.encode_frame(kind, self._sensor_ns, samples))

    async def _run_stream(self, kind: int) -> None:
        i = 0
        while True:
            rate = self.streams.get(kind, (pmd.ECG_RATE_HZ, 8))[0]
            n = 73 if kind == pmd.ECG else max(1, rate // 5)
            await asyncio.sleep(n / rate)
            samples: list = []
            for _ in range(n):
                i += 1
                if kind == pmd.ECG:  # crude P-QRS-T shape, one beat per ~0.8 s
                    ph = (i % 104) - 20
                    samples.append(round(1200 * math.exp(-ph * ph / 3)
                                         - 150 * math.exp(-(ph - 6) ** 2 / 6)
                                         + 120 * math.exp(-(ph - 40) ** 2 / 60)
                                         + 40 * math.exp(-(ph + 15) ** 2 / 20)))
                else:
                    samples.append((round(50 * math.sin(i / 20)), round(30 * math.cos(i / 13)),
                                    round(985 + 20 * math.sin(i / 7))))
            self.emit_frame(kind, samples)

    def force_disconnect(self) -> None:
        self._stop_task()
        cb, self._on_packet = self._on_disconnect, None
        if cb:
            cb()

    async def read_battery(self) -> int | None:
        return self.battery

    async def disconnect(self) -> None:
        self._stop_task()
        self._on_packet = None

    def _stop_task(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        for t in self._stream_tasks.values():
            t.cancel()
        self._stream_tasks.clear()
        self.streams.clear()

    async def _run(self) -> None:
        if self.replay:
            prev = None
            for line in Path(self.replay).read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                t = rec.get("elapsed_ms", 0)
                if prev is not None:
                    await asyncio.sleep(max(0, t - prev) / 1000 / self.replay_speed)
                prev = t
                if self._on_packet:
                    self._on_packet(rec.get("characteristic", "2A37"),
                                    bytes.fromhex(rec["payload_hex"]))
            return
        rr_ms = 800.0
        while True:
            await asyncio.sleep(self.interval_s)
            rr_ms = min(1100.0, max(600.0, rr_ms + self.rng.gauss(0, 25)))
            n = 2 if self.rng.random() < 0.3 else 1
            rrs = [round((rr_ms + self.rng.gauss(0, 15)) * 1024 / 1000) for _ in range(n)]
            if self._on_packet:
                self._on_packet("2A37", encode_hr_measurement(round(60000 / rr_ms), rrs))
