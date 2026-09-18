"""Bluetooth transport: real (bleak) and fake/replay for tests and demos."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

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


def _clean(b: bytes | bytearray) -> str:
    return bytes(b).decode("utf-8", "replace").strip("\x00 ") or ""


class BleTransport:
    """bleak-based transport. Keeps discovered BLEDevice objects for connecting."""

    def __init__(self) -> None:
        self._found: dict[str, object] = {}  # device_id -> BLEDevice
        self._client = None
        self._user_disconnect = False

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
            return details
        except ConnectError:
            raise
        except (BleakError, OSError, asyncio.TimeoutError) as e:
            await self.disconnect()
            raise ConnectError(str(e) or type(e).__name__) from e

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
