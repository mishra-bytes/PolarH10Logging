"""Descriptive time-domain HR/RR metrics: streaming full-session and bounded rolling windows."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

RR_MIN_MS = 300.0
RR_MAX_MS = 2000.0
ROLLING_KEEP_MS = 10 * 60 * 1000
WINDOW_CHOICES: dict[str, int | None] = {
    "60 s": 60, "2 min": 120, "5 min": 300, "10 min": 600, "Full session": None,
}
METHOD_NOTE = (
    "Descriptive estimates from uncorrected RR data; plausibility filter 300-2000 ms; "
    "successive differences never cross a rejected interval or connection gap; not a medical "
    "or diagnostic measurement."
)


def rr_accepted(rr_ms: float) -> bool:
    return RR_MIN_MS <= rr_ms <= RR_MAX_MS


@dataclass(frozen=True)
class WindowMetrics:
    latest_hr: int | None
    hr_mean: float | None
    rr_mean: float | None
    sdnn: float | None
    rmssd: float | None
    pnn50: float | None
    rr_used: int


class _Acc:
    """O(1)-memory accumulator (Welford for RR variance)."""

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.n_diff = 0
        self.sum_sq_diff = 0.0
        self.nn50 = 0
        self.n_hr = 0
        self.hr_sum = 0
        self.hr_min: int | None = None
        self.hr_max: int | None = None

    def add_hr(self, hr: int) -> None:
        self.n_hr += 1
        self.hr_sum += hr
        self.hr_min = hr if self.hr_min is None else min(self.hr_min, hr)
        self.hr_max = hr if self.hr_max is None else max(self.hr_max, hr)

    def add_rr(self, rr: float) -> None:
        self.n += 1
        d = rr - self.mean
        self.mean += d / self.n
        self.m2 += d * (rr - self.mean)

    def add_diff(self, diff: float) -> None:
        self.n_diff += 1
        self.sum_sq_diff += diff * diff
        if abs(diff) > 50.0:
            self.nn50 += 1

    def result(self, latest_hr: int | None) -> WindowMetrics:
        return WindowMetrics(
            latest_hr=latest_hr,
            hr_mean=self.hr_sum / self.n_hr if self.n_hr else None,
            rr_mean=self.mean if self.n else None,
            sdnn=math.sqrt(self.m2 / (self.n - 1)) if self.n >= 2 else None,
            rmssd=math.sqrt(self.sum_sq_diff / self.n_diff) if self.n_diff else None,
            pnn50=100.0 * self.nn50 / self.n_diff if self.n_diff else None,
            rr_used=self.n,
        )


class Metrics:
    """Feed packets in arrival order; query any window at any time."""

    def __init__(self) -> None:
        self._hr: deque[tuple[int, int]] = deque()  # (elapsed_ms, hr)
        # (beat_elapsed_ms, rr_ms, diff_or_None, prev_beat_elapsed_ms_or_None); accepted RR only
        self._rr: deque[tuple[int, float, float | None, int | None]] = deque()
        self._prev: tuple[int, float] | None = None  # last accepted RR in the current chain
        self.latest_hr: int | None = None
        self.reset_full()

    def reset_full(self) -> None:
        """Start a new full-session accumulator (rolling windows are kept)."""
        self.full = _Acc()
        self.packets = 0
        self.rr_count = 0
        self.rr_excluded = 0

    def break_chain(self) -> None:
        """Call on disconnect: no successive difference spans the gap."""
        self._prev = None

    def add_packet(self, elapsed_ms: int, hr: int, rr_ms: tuple[float, ...] | list[float]) -> None:
        self.packets += 1
        self.latest_hr = hr
        self.full.add_hr(hr)
        self._hr.append((elapsed_ms, hr))
        for beat_t, rr in zip(beat_offsets(elapsed_ms, rr_ms), rr_ms):
            self.rr_count += 1
            if not rr_accepted(rr):
                self.rr_excluded += 1
                self._prev = None
                continue
            self.full.add_rr(rr)
            diff = prev_t = None
            if self._prev is not None:
                prev_t, prev_rr = self._prev
                diff = rr - prev_rr
                self.full.add_diff(diff)
            self._rr.append((beat_t, rr, diff, prev_t))
            self._prev = (beat_t, rr)
        cutoff = elapsed_ms - ROLLING_KEEP_MS
        while self._hr and self._hr[0][0] < cutoff:
            self._hr.popleft()
        while self._rr and self._rr[0][0] < cutoff:
            self._rr.popleft()

    def window(self, seconds: int | None) -> WindowMetrics:
        if seconds is None:
            return self.full.result(self.latest_hr)
        if not self._hr:
            return _Acc().result(None)
        cutoff = self._hr[-1][0] - seconds * 1000
        acc = _Acc()
        for t, hr in self._hr:
            if t >= cutoff:
                acc.add_hr(hr)
        for t, rr, diff, prev_t in self._rr:
            if t < cutoff:
                continue
            acc.add_rr(rr)
            if diff is not None and prev_t is not None and prev_t >= cutoff:
                acc.add_diff(diff)
        return acc.result(self.latest_hr)


def beat_offsets(receive_ms: int, rr_ms: tuple[float, ...] | list[float]) -> list[int]:
    """Estimated beat times: the last RR ends at receive time, earlier ones step back."""
    out = []
    after = 0.0
    for rr in reversed(rr_ms):
        out.append(round(receive_ms - after))
        after += rr
    return out[::-1]
