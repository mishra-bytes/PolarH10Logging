import pytest

from polarh10logging.storage import Clock


class FakeClock(Clock):
    def __init__(self, wall_ms: int = 1_789_000_000_000, mono_ms: int = 5_000):
        self.wall = wall_ms
        self.mono = mono_ms
        super().__init__(wall_ms=lambda: self.wall, mono_ms=lambda: self.mono)

    def advance(self, ms: int) -> None:
        self.wall += ms
        self.mono += ms


@pytest.fixture
def clock():
    return FakeClock()
