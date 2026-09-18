import asyncio
import csv
import json

import pytest

from polarh10logging.hr_parser import encode_hr_measurement
from polarh10logging.session import LogOptions, SessionController, State
from polarh10logging.storage import StorageError
from polarh10logging.transport import ConnectError, FakeTransport


class FakeKeepAwake:
    def __init__(self):
        self.active = False

    def acquire(self):
        self.active = True

    def release(self):
        self.active = False


def make(clock, grace_s=120, **kw):
    t = FakeTransport(interval_s=0)
    events = []
    c = SessionController(t, events.append, clock=clock, grace_s=grace_s,
                          reconnect_delays=(0.01,), start_delays=(0, 0, 0),
                          keep_awake=FakeKeepAwake(), **kw)
    return c, t, events


def pkt(hr=70, rr=(820,)):
    return encode_hr_measurement(hr, list(rr))


def csv_rows(folder):
    f = next(folder.glob("HR_*.csv"))
    return list(csv.DictReader(open(f, encoding="utf-8", newline="")))


async def test_connect_live_without_logging_writes_nothing(tmp_path, clock):
    c, t, events = make(clock)
    await c.connect(t.device)
    assert c.state == State.CONNECTED and c.snap.battery == 95
    t.inject(pkt(72))
    assert events[-1].kind == "packet" and events[-1].hr == 72
    assert c.snap.window.latest_hr == 72 and not c.snap.logging
    assert list(tmp_path.iterdir()) == []


async def test_start_stop_log_on_same_connection(tmp_path, clock):
    c, t, events = make(clock)
    await c.connect(t.device)
    t.inject(pkt(60))  # before logging: not written
    folder = c.start_log(LogOptions("P07", tmp_path, condition="baseline"))
    assert c.keep_awake.active
    for hr in (70, 71, 72):
        clock.advance(1000)
        t.inject(pkt(hr))
    assert c.snap.packets == 3 and c.snap.hr_min == 70
    assert c.stop_log() == folder
    assert not c.keep_awake.active and c.state == State.CONNECTED
    rows = csv_rows(folder)
    assert [r["hr_bpm"] for r in rows if r["hr_bpm"]] == ["70", "71", "72"]
    assert all(r["pc_time_utc_iso"].endswith("Z") for r in rows)
    meta = json.loads((folder / "session.json").read_text())
    assert (meta["status"], meta["end_reason"], meta["condition"]) == ("complete", "user_stop",
                                                                       "baseline")
    assert (folder / "app.log").exists()
    # a second log on the same connection gets its own folder
    folder2 = c.start_log(LogOptions("P07", tmp_path))
    assert folder2 != folder
    await c.disconnect()
    assert json.loads((folder2 / "session.json").read_text())["status"] == "complete"
    assert c.state == State.IDLE


async def test_start_log_requires_connection(tmp_path, clock):
    c, t, _ = make(clock)
    with pytest.raises(RuntimeError):
        c.start_log(LogOptions("P07", tmp_path))


async def test_connect_fails_three_times(tmp_path, clock):
    c, t, events = make(clock)
    t.fail_connects = 3
    with pytest.raises(ConnectError):
        await c.connect(t.device)
    assert t.connect_calls == 3 and c.state == State.IDLE
    assert events[-1].kind == "error"


async def test_connect_succeeds_on_third_attempt(clock):
    c, t, _ = make(clock)
    t.fail_connects = 2
    await c.connect(t.device)
    assert c.state == State.CONNECTED


async def test_wrong_model_refused(clock):
    c, t, _ = make(clock)
    t.model = "Verity Sense"
    with pytest.raises(ConnectError):
        await c.connect(t.device)


async def test_reconnect_within_grace_same_folder(tmp_path, clock):
    c, t, events = make(clock, grace_s=5)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))
    t.inject(pkt(70))
    t.force_disconnect()
    await asyncio.sleep(0)
    assert c.state == State.RECONNECTING
    for _ in range(50):
        await asyncio.sleep(0.01)
        if c.state == State.CONNECTED:
            break
    assert c.state == State.CONNECTED and c.snap.logging
    t.inject(pkt(71))
    c.stop_log()
    rows = csv_rows(folder)
    kinds = [r["event"] for r in rows if r["event"]]
    assert kinds[:1] == ["start"] and "disconnect" in kinds and "reconnect" in kinds
    assert [r["segment"] for r in rows if r["hr_bpm"]] == ["1", "2"]


async def test_grace_expires_finalizes_connection_timeout(tmp_path, clock):
    c, t, _ = make(clock, grace_s=0)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))
    t.fail_connects = 99
    t.force_disconnect()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if c.state == State.IDLE:
            break
    assert c.state == State.IDLE and not c.keep_awake.active
    meta = json.loads((folder / "session.json").read_text())
    assert (meta["status"], meta["end_reason"]) == ("complete", "connection_timeout")


async def test_disconnect_during_grace_stops_user(tmp_path, clock):
    c, t, _ = make(clock, grace_s=60, )
    c.reconnect_delays = (30.0,)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))
    t.force_disconnect()
    await asyncio.sleep(0)
    assert c.state == State.RECONNECTING
    await c.disconnect()
    assert c.state == State.IDLE
    assert json.loads((folder / "session.json").read_text())["end_reason"] == "user_stop"


async def test_storage_failure_keeps_live_view(tmp_path, clock):
    c, t, events = make(clock)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))

    def boom(*a):
        raise OSError("disk gone")
    c.writer._raw_f.write = boom
    t.inject(pkt(80))
    assert not c.snap.logging and c.state == State.CONNECTED
    assert "Storage failure" in c.snap.failure and not c.keep_awake.active
    assert json.loads((folder / "session.json").read_text())["status"] == "failed"
    t.inject(pkt(81))
    assert c.snap.window.latest_hr == 81


async def test_malformed_packet_logged_as_error(tmp_path, clock):
    c, t, events = make(clock)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))
    t.inject(b"\x10")
    c.stop_log()
    assert any(r["event"] == "error" for r in csv_rows(folder))
    assert len((folder / "raw.jsonl").read_text().splitlines()) == 1


async def test_battery_out_of_range_not_shown(clock):
    c, t, events = make(clock)
    t.battery = 150
    await c.connect(t.device)
    assert c.snap.battery is None and any(e.kind == "error" for e in events)


async def test_window_change(clock):
    c, t, _ = make(clock)
    await c.connect(t.device)
    c.set_window("5 min")
    assert c.snap.window_label == "5 min"


async def test_replay_raw_jsonl(tmp_path, clock):
    # record with synthetic packets, then replay the raw file through a fresh controller
    c, t, _ = make(clock)
    await c.connect(t.device)
    folder = c.start_log(LogOptions("P07", tmp_path))
    for i in range(5):
        clock.advance(1000)
        t.inject(pkt(70 + i, (800, 810)))
    c.stop_log()
    await c.disconnect()
    t2 = FakeTransport(interval_s=0, replay=folder / "raw.jsonl", replay_speed=1000)
    got = []
    c2 = SessionController(t2, got.append, keep_awake=FakeKeepAwake())
    await c2.connect(t2.device)
    await asyncio.sleep(0.1)
    assert [e.hr for e in got if e.kind == "packet"] == [70, 71, 72, 73, 74]
    assert c2.metrics.rr_count == 10
    await c2.disconnect()
