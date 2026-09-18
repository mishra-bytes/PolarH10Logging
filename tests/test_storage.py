import csv
import json

import pytest

from polarh10logging import storage
from polarh10logging.hr_parser import encode_hr_measurement, parse_hr_measurement
from polarh10logging.metrics import Metrics
from polarh10logging.storage import CSV_HEADER, SessionWriter, StorageError, iso_utc

DEVICE = {"name": "Polar H10 1C3E0231", "device_id": "1C3E0231", "address": "AA",
          "model": "H10", "firmware": "5.0.0", "serial": "1C3E0231"}


def make(tmp_path, clock, pid="P07"):
    return SessionWriter(tmp_path, pid, device=DEVICE, clock=clock, grace_s=120, battery=90)


def rows(w):
    with open(w.folder / w.csv_name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_folder_files_and_header(tmp_path, clock):
    w = make(tmp_path, clock)
    assert w.folder.name.startswith("P07_") and w.csv_name.startswith("HR_P07_")
    with open(w.folder / w.csv_name, encoding="utf-8") as f:
        assert f.readline().rstrip("\n").split(",") == CSV_HEADER
    meta = json.loads((w.folder / "session.json").read_text())
    assert meta["status"] == "recording" and meta["schema_version"] == 3
    assert rows(w)[0]["event"] == "start"


def test_folder_collision_suffix(tmp_path, clock):
    a, b = make(tmp_path, clock), make(tmp_path, clock)
    assert b.folder.name == a.folder.name + "_2"


def test_invalid_participant_creates_nothing(tmp_path, clock):
    with pytest.raises(ValueError):
        make(tmp_path, clock, pid="John Smith")
    assert list(tmp_path.iterdir()) == []


def test_packet_rows_utc_column_and_beat_estimates(tmp_path, clock):
    w = make(tmp_path, clock)
    clock.advance(1000)
    payload = encode_hr_measurement(80, [768, 1024])
    w.write_packet(clock.wall, clock.mono, payload, parse_hr_measurement(payload))
    r = rows(w)[1:]
    assert len(r) == 2
    assert r[0]["pc_time_utc_iso"] == iso_utc(clock.wall) and r[0]["pc_time_utc_iso"].endswith("Z")
    assert int(r[0]["pc_time_unix_ms"]) == clock.wall
    assert (r[0]["rr_ms"], r[1]["rr_ms"]) == ("750.000", "1000.000")
    assert int(r[1]["beat_time_est_unix_ms"]) == clock.wall
    assert int(r[0]["beat_time_est_unix_ms"]) == clock.wall - 1000
    assert r[0]["packet_seq"] == r[1]["packet_seq"] == "1" and r[0]["contact"] == ""
    raw = (w.folder / "raw.jsonl").read_text().splitlines()
    assert json.loads(raw[0])["payload_hex"] == payload.hex()


def test_hr_only_packet_writes_blank_rr_row(tmp_path, clock):
    w = make(tmp_path, clock)
    payload = bytes.fromhex("0057")
    w.write_packet(clock.wall, clock.mono, payload, parse_hr_measurement(payload))
    r = rows(w)[-1]
    assert r["hr_bpm"] == "87" and r["rr_ms"] == "" and r["rr_index"] == ""


def test_unparsable_payload_goes_to_raw_only(tmp_path, clock):
    w = make(tmp_path, clock)
    w.write_packet(clock.wall, clock.mono, b"\x10", None)
    assert len(rows(w)) == 1 and w.counts["packets"] == 0
    assert len((w.folder / "raw.jsonl").read_text().splitlines()) == 1


def test_disconnect_segments_and_connected_time(tmp_path, clock):
    w = make(tmp_path, clock)
    clock.advance(10_000)
    w.disconnected("link lost")
    clock.advance(5_000)
    w.reconnected()
    clock.advance(5_000)
    assert w.segment == 2 and w.counts["disconnects"] == 1
    dur, conn = w.durations()
    assert (dur, conn) == (20.0, 15.0)


def test_finalize_writes_summary_and_complete(tmp_path, clock):
    w = make(tmp_path, clock)
    m = Metrics()
    for i in range(5):
        clock.advance(800)
        p = encode_hr_measurement(75, [820])
        s = parse_hr_measurement(p)
        w.write_packet(clock.wall, clock.mono, p, s)
        m.add_packet(clock.mono, s.hr_bpm, s.rr_ms)
    w.finalize("complete", "user_stop", m.window(None), m.full.hr_min, m.full.hr_max)
    meta = json.loads((w.folder / "session.json").read_text())
    assert meta["status"] == "complete" and meta["end_reason"] == "user_stop"
    assert meta["counts"]["packets"] == 5 and meta["counts"]["rr"] == 5
    assert "summary.csv" in meta["files"]
    summ = dict(csv.reader(open(w.folder / "summary.csv", encoding="utf-8")))
    assert summ["packets"] == "5" and summ["hr_mean"] == "75.0" and summ["rmssd_ms"] == "0.0"
    assert rows(w)[-1]["event"] == "stop"


def test_summary_blank_when_not_computable(tmp_path, clock):
    w = make(tmp_path, clock)
    m = Metrics()
    w.finalize("complete", "user_stop", m.window(None), None, None)
    summ = dict(csv.reader(open(w.folder / "summary.csv", encoding="utf-8")))
    assert summ["sdnn_ms"] == "" and summ["hr_min"] == ""


def test_fsync_cadence(tmp_path, clock, monkeypatch):
    calls = []
    real = storage.os.fsync
    monkeypatch.setattr(storage.os, "fsync", lambda fd: (calls.append(fd), real(fd)))
    w = make(tmp_path, clock)
    calls.clear()
    p = encode_hr_measurement(70, [800])
    w.write_packet(clock.wall, clock.mono + 1000, p, parse_hr_measurement(p))
    assert calls == []
    w.write_packet(clock.wall, clock.mono + 5000, p, parse_hr_measurement(p))
    assert len(calls) == 2


def test_write_failure_raises_storage_error(tmp_path, clock):
    w = make(tmp_path, clock)

    def boom(*a):
        raise OSError("disk gone")
    w._raw_f.write = boom
    with pytest.raises(StorageError):
        w.write_packet(clock.wall, clock.mono, b"\x00\x40", None)


def test_finalize_continues_after_failure(tmp_path, clock):
    w = make(tmp_path, clock)

    def boom(*a):
        raise OSError("disk gone")
    w._csv_f.flush = boom
    with pytest.raises(StorageError):
        w.finalize("failed", "storage_failure", Metrics().window(None), None, None)
    assert (w.folder / "summary.csv").exists()
    assert json.loads((w.folder / "session.json").read_text())["status"] == "failed"


def test_output_root_unwritable(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(StorageError, match="250 MiB"):
        storage.check_output_root(tmp_path)
