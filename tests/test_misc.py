import logging

from polarh10logging.config import AppConfig, load_config, save_config
from polarh10logging.keepawake import KeepAwake
from polarh10logging.logsetup import attach_session_log, detach_session_log
from polarh10logging.transport import DeviceInfo, device_id_from_name


def test_config_roundtrip_and_clamp(tmp_path):
    p = tmp_path / "c" / "config.json"
    assert load_config(p) == AppConfig()
    save_config(AppConfig(output_root="X", grace_s=300, last_device_id="1C3E0231"), p)
    assert load_config(p).grace_s == 300 and load_config(p).last_device_id == "1C3E0231"
    p.write_text('{"grace_s": 999999, "unknown": 1}')
    assert load_config(p).grace_s == 86400
    p.write_text("not json")
    assert load_config(p) == AppConfig()


def test_device_id_from_name():
    assert device_id_from_name("Polar H10 1C3E0231") == "1C3E0231"
    assert device_id_from_name("WHOOP 5B00447320") is None
    assert device_id_from_name("Polar H10") is None
    assert DeviceInfo("Polar H10 A", "x", "A", -40).label == "Polar H10 A  (-40 dBm)"


def test_session_log_attach(tmp_path):
    h = attach_session_log(tmp_path)
    logging.getLogger("x").warning("hello")
    detach_session_log(h)
    assert "hello" in (tmp_path / "app.log").read_text()
    assert attach_session_log(tmp_path / "missing") is None


def test_keepawake_idempotent():
    k = KeepAwake()
    k.acquire(); k.acquire()
    assert k.active
    k.release(); k.release()
    assert not k.active
