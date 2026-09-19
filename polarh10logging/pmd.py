"""Polar Measurement Data (PMD) protocol: H10 raw ECG and accelerometer streams."""

from __future__ import annotations

from dataclasses import dataclass

PMD_SERVICE_UUID = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CONTROL_UUID = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA_UUID = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"

ECG = 0x00
ACC = 0x02
STREAM_NAMES = {ECG: "ECG", ACC: "ACC"}

OP_GET_SETTINGS = 0x01
OP_START = 0x02
OP_STOP = 0x03
SET_SAMPLE_RATE = 0x00
SET_RESOLUTION = 0x01
SET_RANGE = 0x02

ECG_RATE_HZ = 130
ECG_RESOLUTION = 14
ACC_RATES_HZ = (25, 50, 100, 200)
ACC_RANGES_G = (2, 4, 8)
ACC_RESOLUTION = 16

STATUS_TEXT = {0: "success", 1: "invalid op code", 2: "invalid measurement type",
               3: "not supported", 4: "invalid length", 5: "invalid parameter",
               6: "already in state", 7: "invalid resolution", 8: "invalid sample rate",
               9: "invalid range", 10: "invalid MTU", 11: "invalid number of channels",
               12: "invalid state", 13: "device in charger"}


class PmdError(Exception):
    """A PMD command was refused or a frame could not be decoded."""


@dataclass(frozen=True)
class PmdFrame:
    kind: int  # ECG or ACC
    sensor_ns: int  # sensor timestamp of the last sample in the frame
    frame_type: int
    samples: tuple  # ECG: (uV, ...); ACC: ((x_mg, y_mg, z_mg), ...)


def _setting(kind: int, value: int) -> bytes:
    return bytes([kind, 1]) + value.to_bytes(2, "little")


def start_ecg_command() -> bytes:
    return bytes([OP_START, ECG]) + _setting(SET_SAMPLE_RATE, ECG_RATE_HZ) + \
        _setting(SET_RESOLUTION, ECG_RESOLUTION)


def start_acc_command(rate_hz: int, range_g: int) -> bytes:
    if rate_hz not in ACC_RATES_HZ:
        raise ValueError(f"accelerometer rate must be one of {ACC_RATES_HZ}")
    if range_g not in ACC_RANGES_G:
        raise ValueError(f"accelerometer range must be one of {ACC_RANGES_G}")
    return bytes([OP_START, ACC]) + _setting(SET_SAMPLE_RATE, rate_hz) + \
        _setting(SET_RESOLUTION, ACC_RESOLUTION) + _setting(SET_RANGE, range_g)


def stop_command(kind: int) -> bytes:
    return bytes([OP_STOP, kind])


def supported_streams(features: bytes) -> set[int]:
    """Decode the control-point read: 0x0F then a bitmask of measurement types."""
    if len(features) < 2 or features[0] != 0x0F:
        return set()
    mask = int.from_bytes(features[1:3], "little")
    return {t for t in range(16) if mask >> t & 1}


def check_response(resp: bytes, op: int, kind: int) -> None:
    """Raise PmdError unless `resp` is a success response to (op, kind)."""
    if len(resp) < 4 or resp[0] != 0xF0 or resp[1] != op or resp[2] != kind:
        raise PmdError(f"unexpected control-point response {resp.hex()}")
    if resp[3] != 0:
        raise PmdError(f"{STREAM_NAMES.get(kind, kind)} refused: "
                       f"{STATUS_TEXT.get(resp[3], resp[3])}")


def parse_frame(data: bytes) -> PmdFrame:
    if len(data) < 10:
        raise PmdError(f"PMD frame too short ({len(data)} bytes)")
    kind, ts, ftype = data[0], int.from_bytes(data[1:9], "little"), data[9]
    body = data[10:]
    if kind == ECG and ftype == 0x00:
        if len(body) % 3:
            raise PmdError("ECG frame length not a multiple of 3")
        samples: tuple = tuple(int.from_bytes(body[i:i + 3], "little", signed=True)
                               for i in range(0, len(body), 3))
    elif kind == ACC and ftype in (0x00, 0x01, 0x02):
        w = ftype + 1
        if len(body) % (3 * w):
            raise PmdError("ACC frame length not a multiple of the sample size")
        v = [int.from_bytes(body[i:i + w], "little", signed=True) for i in range(0, len(body), w)]
        samples = tuple(zip(v[0::3], v[1::3], v[2::3]))
    else:
        raise PmdError(f"unsupported PMD frame: type {kind} frame type {ftype:#x}")
    return PmdFrame(kind, ts, ftype, samples)


def encode_frame(kind: int, sensor_ns: int, samples) -> bytes:
    """Inverse of parse_frame for uncompressed frames; used by tests and the fake transport."""
    out = bytearray([kind]) + sensor_ns.to_bytes(8, "little")
    if kind == ECG:
        out.append(0x00)
        for s in samples:
            out += int(s).to_bytes(3, "little", signed=True)
    else:
        out.append(0x01)
        for x, y, z in samples:
            for c in (x, y, z):
                out += int(c).to_bytes(2, "little", signed=True)
    return bytes(out)


class SampleClock:
    """Per-sample times from sensor timestamps, anchored to PC wall time.

    The H10 stamps the last sample of each frame with its own nanosecond clock (not set to
    real time). The first frame after (re)connecting anchors sensor time to the PC receive
    time; later samples keep the sensor's precise spacing. Absolute times are therefore
    estimates that include Bluetooth delivery latency at the anchor.
    """

    def __init__(self, nominal_hz: float) -> None:
        self.nominal_hz = nominal_hz
        self.offset_ns: int | None = None
        self.prev_ns: int | None = None

    def reset(self) -> None:
        self.offset_ns = None
        self.prev_ns = None

    def sample_times(self, frame: PmdFrame, pc_wall_ms: int) -> list[tuple[int, float]]:
        """Return [(sensor_ns, unix_ms_float), ...] for each sample in the frame."""
        n = len(frame.samples)
        if n == 0:
            return []
        last = frame.sensor_ns
        if self.offset_ns is None or (self.prev_ns is not None and last <= self.prev_ns):
            self.offset_ns = pc_wall_ms * 1_000_000 - last
            self.prev_ns = None
        nominal = 1e9 / self.nominal_hz
        if self.prev_ns is not None:
            step = (last - self.prev_ns) / n
            if not 0.5 * nominal <= step <= 1.5 * nominal:  # gap or glitch: use nominal rate
                step = nominal
        else:
            step = nominal
        self.prev_ns = last
        out = []
        for i in range(n):
            ns = round(last - (n - 1 - i) * step)
            out.append((ns, (ns + self.offset_ns) / 1e6))
        return out
