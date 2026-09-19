import pytest

from polarh10logging import pmd
from polarh10logging.pmd import ACC, ECG, PmdError, PmdFrame, SampleClock


def test_start_commands_match_bytes_accepted_by_real_h10():
    assert pmd.start_ecg_command().hex() == "02000001820001010e00"
    assert pmd.start_acc_command(200, 8).hex() == "02020001c8000101100002010800"
    assert pmd.start_acc_command(25, 8).hex() == "020200011900010110000201080 0".replace(" ", "")
    assert pmd.stop_command(ACC) == b"\x03\x02"


@pytest.mark.parametrize("rate,rng", [(30, 8), (200, 16)])
def test_invalid_acc_settings(rate, rng):
    with pytest.raises(ValueError):
        pmd.start_acc_command(rate, rng)


def test_supported_streams_from_real_feature_read():
    feat = bytes.fromhex("0f05000000000000000000000000000000")  # H10 fw 5.0.0
    assert pmd.supported_streams(feat) == {ECG, ACC}
    assert pmd.supported_streams(b"") == set()


def test_check_response():
    pmd.check_response(bytes.fromhex("f00200000000"), pmd.OP_START, ECG)
    pmd.check_response(bytes.fromhex("f00202000001"), pmd.OP_START, ACC)
    with pytest.raises(PmdError, match="not supported"):
        pmd.check_response(bytes.fromhex("f0020003"), pmd.OP_START, ECG)
    with pytest.raises(PmdError, match="unexpected"):
        pmd.check_response(bytes.fromhex("f0030000"), pmd.OP_START, ECG)


def test_ecg_frame_roundtrip_signed_24bit():
    samples = (-656, 965, 0, -1, 8_000_000)
    f = pmd.parse_frame(pmd.encode_frame(ECG, 123_456_789, samples))
    assert f == PmdFrame(ECG, 123_456_789, 0, samples)


def test_real_frame_sizes():
    # H10: ECG frames are 229 bytes (73 samples), ACC 16-bit frames 226 bytes (36 samples)
    assert len(pmd.parse_frame(pmd.encode_frame(ECG, 1, [0] * 73)).samples) == 73
    assert len(pmd.encode_frame(ECG, 1, [0] * 73)) == 229
    assert len(pmd.encode_frame(ACC, 1, [(0, 0, 0)] * 36)) == 226


def test_acc_frame_roundtrip():
    s = ((-216, 12, 968), (1, -2, 3))
    assert pmd.parse_frame(pmd.encode_frame(ACC, 5, s)).samples == s


@pytest.mark.parametrize("data", [b"\x00" * 5, bytes([0, *[0] * 8, 0x80, 1, 2, 3]),
                                  bytes([0, *[0] * 8, 0, 1, 2])])
def test_bad_frames(data):
    with pytest.raises(PmdError):
        pmd.parse_frame(data)


def test_sample_clock_anchor_and_spacing():
    c = SampleClock(130)
    step = 561_000_000 // 73
    f1 = PmdFrame(ECG, 10_000_000_000, 0, (0,) * 73)
    t1 = c.sample_times(f1, pc_wall_ms=1_789_000_000_000)
    assert t1[-1] == (10_000_000_000, 1_789_000_000_000.0)
    f2 = PmdFrame(ECG, 10_000_000_000 + 561_000_000, 0, (0,) * 73)
    t2 = c.sample_times(f2, pc_wall_ms=1_789_000_000_999)  # PC jitter is ignored
    assert t2[-1][1] == pytest.approx(1_789_000_000_561.0)
    assert t2[1][0] - t2[0][0] == pytest.approx(step, abs=1)
    assert t2[0][0] > t1[-1][0]


def test_sample_clock_reanchors_when_sensor_time_goes_back():
    c = SampleClock(25)
    c.sample_times(PmdFrame(ACC, 5_000_000_000, 1, ((0, 0, 0),)), 1000)
    t = c.sample_times(PmdFrame(ACC, 1_000_000_000, 1, ((0, 0, 0),)), 9000)
    assert t[-1][1] == 9000.0
