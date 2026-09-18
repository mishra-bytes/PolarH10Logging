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
