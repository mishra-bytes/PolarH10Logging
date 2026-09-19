import csv
import json

from polarh10logging import cli
from polarh10logging.cli import EXIT_INPUT, EXIT_OK, main


def test_fake_record_short_session(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "STATUS_EVERY_S", 0.05)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    code = main(["--record", "--fake", "--participant", "P01", "--out", str(tmp_path / "out"),
                 "--duration", str(0.4 / 60)])
    assert code == EXIT_OK
    folder = next((tmp_path / "out").iterdir())
    meta = json.loads((folder / "session.json").read_text())
    assert (meta["status"], meta["end_reason"]) == ("complete", "duration_elapsed")


def test_invalid_participant(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert main(["--record", "--fake", "--participant", "bad name",
                 "--out", str(tmp_path)]) == EXIT_INPUT


def test_scan_fake(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert main(["--scan", "--fake"]) == EXIT_OK
    assert "FAKE0001" in capsys.readouterr().out


def test_fake_record_with_ecg_and_acc(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "STATUS_EVERY_S", 0.05)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    code = main(["--record", "--fake", "--participant", "P02", "--out", str(tmp_path / "out"),
                 "--duration", str(1.2 / 60), "--ecg", "--acc", "--acc-rate", "100"])
    assert code == EXIT_OK
    folder = next((tmp_path / "out").iterdir())
    assert next(folder.glob("ECG_P02_*.csv")).stat().st_size > 100
    assert next(folder.glob("ACC_P02_*.csv")).stat().st_size > 100
    meta = json.loads((folder / "session.json").read_text())
    assert meta["streams"]["ACC"]["sample_rate_hz"] == 100
