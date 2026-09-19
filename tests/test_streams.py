import asyncio
import csv
import json

import pytest

from polarh10logging import pmd
from polarh10logging.session import LogOptions, SessionController, State, StreamConfig
from polarh10logging.storage import ACC_HEADER, ECG_HEADER
from polarh10logging.transport import FakeTransport
from tests.test_session import FakeKeepAwake, csv_rows, make, pkt


def stream_rows(folder, prefix):
    f = next(folder.glob(f"{prefix}_*.csv"))
    return list(csv.DictReader(open(f, encoding="utf-8", newline="")))


async def test_streams_start_on_connect_and_log_to_own_files(tmp_path, clock):
    c, t, events = make(clock)
    await c.set_streams(StreamConfig(ecg=True, acc=True, acc_rate_hz=100, acc_range_g=4))
    await c.connect(t.device)
    assert t.streams == {pmd.ECG: (130, 0), pmd.ACC: (100, 4)}
    assert c.snap.streams_active == {pmd.ECG, pmd.ACC}
    folder = c.start_log(LogOptions("P07", tmp_path))
    clock.advance(1000)
    t.emit_frame(pmd.ECG, [100, -200, 300])
    t.emit_frame(pmd.ACC, [(1, 2, 980), (3, 4, 981)])
    t.inject(pkt(70))
    ev = [e for e in events if e.kind == "pmd"]
    assert ev[0].stream == pmd.ECG and ev[0].values == (100, -200, 300)
    assert c.snap.ecg_samples == 3 and c.snap.acc_samples == 2
    c.stop_log()
    ecg = stream_rows(folder, "ECG")
    assert list(ecg[0]) == ECG_HEADER and [r["ecg_uv"] for r in ecg] == ["100", "-200", "300"]
    assert ecg[-1]["sample_time_utc_iso"].endswith("Z")
    assert float(ecg[-1]["sample_time_unix_ms"]) == clock.wall
    t_ms = [float(r["sample_time_unix_ms"]) for r in ecg]
    assert t_ms == sorted(t_ms) and t_ms[1] - t_ms[0] == pytest.approx(1000 / 130, abs=0.01)
    acc = stream_rows(folder, "ACC")
    assert list(acc[0]) == ACC_HEADER and (acc[1]["x_mg"], acc[1]["z_mg"]) == ("3", "981")
    meta = json.loads((folder / "session.json").read_text())
    assert meta["streams"]["ACC"] == {"sample_rate_hz": 100, "range_g": 4,
                                      "resolution_bits": 16, "unit": "mG", "active": True}
    assert meta["counts"]["ecg_samples"] == 3
    assert any(r["event"] == "stream" and "ECG started" in r["detail"]
               for r in csv_rows(folder))
    summ = dict(csv.reader(open(folder / "summary.csv", encoding="utf-8")))
    assert (summ["ecg_samples"], summ["acc_samples"]) == ("3", "2")
    raw = [json.loads(x) for x in (folder / "raw.jsonl").read_text().splitlines()]
    assert {r["characteristic"] for r in raw} == {"PMD", "2A37"}


async def test_hr_only_by_default_creates_no_stream_files(tmp_path, clock):
    c, t, _ = make(clock)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))
    t.inject(pkt(70))
    c.stop_log()
    assert t.streams == {} and not list(folder.glob("ECG_*")) and not list(folder.glob("ACC_*"))


async def test_toggle_streams_while_connected(tmp_path, clock):
    c, t, _ = make(clock)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))
    await c.set_streams(StreamConfig(acc=True, acc_rate_hz=25))
    assert t.streams == {pmd.ACC: (25, 8)}
    await c.set_streams(StreamConfig(acc=True, acc_rate_hz=200))  # restart with new rate
    assert t.streams == {pmd.ACC: (200, 8)}
    await c.set_streams(StreamConfig())
    assert t.streams == {} and c.snap.streams_active == frozenset()
    c.stop_log()
    details = [r["detail"] for r in csv_rows(folder) if r["event"] == "stream"]
    assert details[0].startswith("ACC started") and "ACC stopped" in details


async def test_unsupported_stream_warns_and_hr_continues(clock):
    c, t, events = make(clock)
    t.pmd_supported = {pmd.ACC}
    await c.set_streams(StreamConfig(ecg=True))
    await c.connect(t.device)
    assert c.state == State.CONNECTED and pmd.ECG not in c.snap.streams_active
    assert any(e.kind == "warning" and "ECG" in e.detail for e in events)


async def test_invalid_acc_rate_rejected(clock):
    c, _, _ = make(clock)
    with pytest.raises(ValueError):
        await c.set_streams(StreamConfig(acc=True, acc_rate_hz=33))


async def test_streams_restart_after_reconnect(tmp_path, clock):
    c, t, _ = make(clock, grace_s=5)
    await c.set_streams(StreamConfig(ecg=True))
    await c.connect(t.device)
    starts = t.stream_starts
    t.force_disconnect()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if t.stream_starts > starts:
            break
    assert c.state == State.CONNECTED and t.stream_starts == starts + 1
    assert pmd.ECG in t.streams


async def test_bad_pmd_frame_reported_once_and_kept_raw(tmp_path, clock):
    c, t, events = make(clock)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))
    for _ in range(3):
        t.inject(bytes([0, *[0] * 8, 0x80, 1, 2, 3]), char="PMD")
    c.stop_log()
    assert sum(1 for e in events if e.kind == "error" and "sensor frame" in e.detail) == 1
    assert len((folder / "raw.jsonl").read_text().splitlines()) == 3


async def test_fake_generator_streams(clock):
    t = FakeTransport(interval_s=1.0)
    got = []
    c = SessionController(t, got.append, keep_awake=FakeKeepAwake())
    await c.set_streams(StreamConfig(ecg=True, acc=True, acc_rate_hz=200))
    await c.connect(t.device)
    await asyncio.sleep(0.7)
    await c.disconnect()
    kinds = {e.stream for e in got if e.kind == "pmd"}
    assert kinds == {pmd.ECG, pmd.ACC}
