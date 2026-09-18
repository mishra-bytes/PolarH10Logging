import math

import pytest

from polarh10logging.metrics import Metrics, beat_offsets


def feed(m, t0, rrs, hr=60):
    t = t0
    for rr in rrs:
        t += rr
        m.add_packet(round(t), hr, [rr])
    return t


def test_full_session_basic_stats():
    m = Metrics()
    feed(m, 0, [800, 810, 790, 900])
    w = m.window(None)
    rr = [800, 810, 790, 900]
    mean = sum(rr) / 4
    sd = math.sqrt(sum((x - mean) ** 2 for x in rr) / 3)
    diffs = [10, -20, 110]
    assert w.rr_mean == pytest.approx(mean)
    assert w.sdnn == pytest.approx(sd)
    assert w.rmssd == pytest.approx(math.sqrt(sum(d * d for d in diffs) / 3))
    assert w.pnn50 == pytest.approx(100 / 3)
    assert w.rr_used == 4 and m.rr_count == 4 and m.rr_excluded == 0


def test_excluded_rr_breaks_chain():
    m = Metrics()
    feed(m, 0, [800, 2500, 820])
    w = m.window(None)
    assert m.rr_excluded == 1 and w.rr_used == 2
    assert w.rmssd is None  # the only pair spans a rejected interval


def test_disconnect_breaks_chain():
    m = Metrics()
    t = feed(m, 0, [800, 820])
    m.break_chain()
    feed(m, t + 10_000, [900])
    assert m.window(None).rmssd == pytest.approx(20)


def test_rolling_window_only_recent():
    m = Metrics()
    t = feed(m, 0, [1000] * 120, hr=60)  # 120 s at 60 bpm
    feed(m, t, [500] * 60, hr=120)  # next 30 s at 120 bpm
    w = m.window(30)
    assert w.hr_mean == pytest.approx(120, abs=3)
    assert m.window(None).hr_mean < 100


def test_rolling_memory_bounded_to_10_minutes():
    m = Metrics()
    feed(m, 0, [1000] * 1500)
    assert len(m._rr) <= 601 and len(m._hr) <= 601


def test_empty_values_are_none_not_zero():
    w = Metrics().window(60)
    assert w.hr_mean is None and w.sdnn is None and w.rmssd is None


def test_reset_full_keeps_rolling():
    m = Metrics()
    feed(m, 0, [800, 810])
    m.reset_full()
    assert m.window(None).rr_used == 0 and m.window(60).rr_used == 2


def test_beat_offsets_last_ends_at_receive():
    assert beat_offsets(10_000, [700.0, 800.0]) == [9200, 10_000]
