import csv
import json

from polarh10logging.hr_parser import encode_hr_measurement, parse_hr_measurement
from polarh10logging.recover import find_unfinished, recover_interrupted, recover_session
from polarh10logging.storage import SessionWriter

DEVICE = {"device_id": "1C3E0231", "model": "H10"}


def crashed(tmp_path, clock, n=5):
    w = SessionWriter(tmp_path, "P07", device=DEVICE, clock=clock)
    for i in range(n):
        clock.advance(1000)
        p = encode_hr_measurement(70 + i, [820, 830])
        w.write_packet(clock.wall, clock.mono, p, parse_hr_measurement(p))
    w._csv_f.flush()
    return w  # never finalized: status stays "recording"


def test_find_and_recover(tmp_path, clock):
    w = crashed(tmp_path, clock)
    done = SessionWriter(tmp_path, "P08", device=DEVICE, clock=clock)
    done.finalize("complete", "user_stop", __import__("polarh10logging.metrics").metrics
                  .Metrics().window(None), None, None)
    assert find_unfinished(tmp_path) == [w.folder]
    meta = recover_session(w.folder)
    assert (meta["status"], meta["end_reason"]) == ("interrupted", "process_interrupted")
    assert meta["counts"]["packets"] == 5 and meta["counts"]["rr"] == 10
    summ = dict(csv.reader(open(w.folder / "summary.csv", encoding="utf-8")))
    assert summ["hr_min"] == "70" and summ["hr_max"] == "74" and summ["rmssd_ms"] != ""
    assert find_unfinished(tmp_path) == []
    # idempotent
    assert recover_session(w.folder)["counts"] == meta["counts"]


def test_torn_line_and_missing_metadata(tmp_path, clock):
    w = crashed(tmp_path, clock)
    w.close_quietly()
    (w.folder / "session.json").unlink()
    with open(w.folder / w.csv_name, "a", encoding="utf-8", newline="") as f:
        f.write("P07_x,P07,2026-09-19T10")  # torn row, no newline
    assert recover_interrupted(tmp_path) == [w.folder]
    meta = json.loads((w.folder / "session.json").read_text())
    assert meta["status"] == "interrupted" and meta["counts"]["packets"] == 5
    assert meta["participant_id"] == "P07"
