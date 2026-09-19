"""BleTransport PMD logic against a stand-in client (no bleak calls)."""
import asyncio

import pytest

from polarh10logging import pmd
from polarh10logging.transport import BleTransport


class StubServices:
    def __init__(self, has_pmd):
        self.has_pmd = has_pmd

    def get_service(self, uuid):
        return object() if self.has_pmd and uuid == pmd.PMD_SERVICE_UUID else None


class StubClient:
    """Answers PMD control-point writes the way the H10 did in hardware tests."""

    def __init__(self, has_pmd=True, refuse=None, silent=False):
        self.services = StubServices(has_pmd)
        self.writes = []
        self.notify = {}
        self.refuse = refuse or {}
        self.silent = silent
        self.running = set()

    async def read_gatt_char(self, uuid):
        return bytes.fromhex("0f05000000000000000000000000000000")

    async def start_notify(self, uuid, cb):
        self.notify[uuid] = cb

    async def write_gatt_char(self, uuid, payload, response=True):
        self.writes.append(bytes(payload).hex())
        if self.silent:
            return
        op, kind = payload[0], payload[1]
        status = self.refuse.get(bytes(payload).hex(), 0)
        if status == 0 and op == pmd.OP_START and kind in self.running:
            status = 6
        if status == 0 and op == pmd.OP_START:
            self.running.add(kind)
        if op == pmd.OP_STOP:
            status = 0 if kind in self.running else 6
            self.running.discard(kind)
        self.notify[pmd.PMD_CONTROL_UUID](None, bytearray([0xF0, op, kind, status, 0]))


async def ready(client):
    t = BleTransport()
    t._client = client
    got = []
    await t._setup_pmd(lambda char, data: got.append((char, data)))
    return t, got


async def test_setup_reports_ecg_and_acc_and_routes_data():
    c = StubClient()
    t, got = await ready(c)
    assert t.pmd_streams() == {pmd.ECG, pmd.ACC}
    c.notify[pmd.PMD_DATA_UUID](None, bytearray(b"\x00abc"))
    assert got == [("PMD", b"\x00abc")]


async def test_no_pmd_service():
    t, _ = await ready(StubClient(has_pmd=False))
    assert t.pmd_streams() == set()


async def test_start_and_stop_commands():
    c = StubClient()
    t, _ = await ready(c)
    await t.start_stream(pmd.ECG)
    await t.start_stream(pmd.ACC, 200, 8)
    await t.stop_stream(pmd.ACC)
    assert c.writes == ["02000001820001010e00", "02020001c8000101100002010800", "0302"]


async def test_already_running_is_restarted_with_new_settings():
    c = StubClient()
    t, _ = await ready(c)
    await t.start_stream(pmd.ACC, 25, 8)
    await t.start_stream(pmd.ACC, 100, 8)
    assert c.writes[-2:] == ["0302", "0202000164000101100002010800"]


async def test_refused_start_raises():
    c = StubClient(refuse={"02000001820001010e00": 3})
    t, _ = await ready(c)
    with pytest.raises(pmd.PmdError, match="not supported"):
        await t.start_stream(pmd.ECG)


async def test_unsupported_kind_raises():
    t, _ = await ready(StubClient())
    t._pmd = {pmd.ACC}
    with pytest.raises(pmd.PmdError):
        await t.start_stream(pmd.ECG)


async def test_no_response_times_out(monkeypatch):
    async def fast_wait_for(aw, timeout):
        aw.close()
        raise asyncio.TimeoutError
    monkeypatch.setattr("polarh10logging.transport.asyncio.wait_for", fast_wait_for)
    t, _ = await ready(StubClient(silent=True))
    with pytest.raises(pmd.PmdError, match="no response"):
        await t.start_stream(pmd.ECG)
    await t.stop_stream(pmd.ECG)  # logged, not raised


async def test_link_tuning_is_best_effort():
    t, _ = await ready(StubClient())
    t._tune_link()  # stub has no WinRT device: must not raise
    assert t._link_tuned
