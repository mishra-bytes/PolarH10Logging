"""Bluetooth SIG Heart Rate Measurement (0x2A37) parser and test encoder."""

from __future__ import annotations

from dataclasses import dataclass

FLAG_HR_16BIT = 0x01
FLAG_CONTACT_DETECTED = 0x02
FLAG_CONTACT_SUPPORTED = 0x04
FLAG_ENERGY = 0x08
FLAG_RR = 0x10


class HrParseError(ValueError):
    """The payload is not a valid Heart Rate Measurement."""


@dataclass(frozen=True)
class HrSample:
    flags: int
    hr_bpm: int
    contact: bool | None  # None when the packet does not report contact
    energy_kj: int | None
    rr_raw: tuple[int, ...]  # units of 1/1024 s

    @property
    def rr_ms(self) -> tuple[float, ...]:
        return tuple(rr_raw_to_ms(r) for r in self.rr_raw)


def rr_raw_to_ms(raw: int) -> float:
    return round(raw * 1000 / 1024, 3)


def parse_hr_measurement(data: bytes) -> HrSample:
    if len(data) < 2:
        raise HrParseError(f"payload too short ({len(data)} bytes)")
    flags = data[0]
    i = 1
    if flags & FLAG_HR_16BIT:
        if len(data) < 3:
            raise HrParseError("16-bit HR flag set but payload truncated")
        hr = int.from_bytes(data[1:3], "little")
        i = 3
    else:
        hr = data[1]
        i = 2
    contact = bool(flags & FLAG_CONTACT_DETECTED) if flags & FLAG_CONTACT_SUPPORTED else None
    energy = None
    if flags & FLAG_ENERGY:
        if len(data) < i + 2:
            raise HrParseError("energy flag set but payload truncated")
        energy = int.from_bytes(data[i:i + 2], "little")
        i += 2
    rr: list[int] = []
    if flags & FLAG_RR:
        rest = data[i:]
        if len(rest) % 2:
            raise HrParseError("odd number of bytes in RR field")
        rr = [int.from_bytes(rest[j:j + 2], "little") for j in range(0, len(rest), 2)]
    return HrSample(flags, hr, contact, energy, tuple(rr))


def encode_hr_measurement(hr: int, rr_raw: list[int] | tuple[int, ...] = (), *,
                          contact: bool | None = None, energy: int | None = None) -> bytes:
    """Build a payload; used by tests and the fake transport."""
    flags = 0
    body = bytearray()
    if hr > 255:
        flags |= FLAG_HR_16BIT
        body += hr.to_bytes(2, "little")
    else:
        body.append(hr)
    if contact is not None:
        flags |= FLAG_CONTACT_SUPPORTED | (FLAG_CONTACT_DETECTED if contact else 0)
    if energy is not None:
        flags |= FLAG_ENERGY
        body += energy.to_bytes(2, "little")
    if rr_raw:
        flags |= FLAG_RR
        for r in rr_raw:
            body += int(r).to_bytes(2, "little")
    return bytes([flags]) + bytes(body)
