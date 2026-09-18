import pytest

from polarh10logging.hr_parser import (HrParseError, encode_hr_measurement,
                                       parse_hr_measurement, rr_raw_to_ms)


def test_real_h10_packet_flags_0x10():
    s = parse_hr_measurement(bytes.fromhex("10530b03"))
    assert (s.flags, s.hr_bpm, s.contact, s.energy_kj, s.rr_raw) == (0x10, 83, None, None, (779,))
    assert s.rr_ms == (760.742,)


def test_hr_only_packet_flags_0x00():
    s = parse_hr_measurement(bytes.fromhex("0057"))
    assert s.hr_bpm == 87 and s.rr_raw == () and s.contact is None


def test_two_rr_intervals():
    s = parse_hr_measurement(bytes([0x10, 80, 0x00, 0x03, 0x10, 0x03]))
    assert s.rr_raw == (768, 784)


@pytest.mark.parametrize("contact", [True, False])
def test_contact_flags(contact):
    assert parse_hr_measurement(encode_hr_measurement(70, contact=contact)).contact is contact


def test_16bit_hr_energy_and_rr_roundtrip():
    data = encode_hr_measurement(300, [1024, 512], energy=1234, contact=True)
    s = parse_hr_measurement(data)
    assert (s.hr_bpm, s.energy_kj, s.rr_raw, s.contact) == (300, 1234, (1024, 512), True)
    assert s.rr_ms == (1000.0, 500.0)


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x01\x2c", b"\x08\x46\x10", b"\x10\x46\x00"])
def test_truncated_payloads_raise(data):
    with pytest.raises(HrParseError):
        parse_hr_measurement(data)


def test_rr_conversion_rounds_to_3_decimals():
    assert rr_raw_to_ms(1) == 0.977
