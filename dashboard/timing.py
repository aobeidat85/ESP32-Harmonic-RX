"""Conservative burst-period tracking, separate from packet validation."""

import math
from collections import deque
from statistics import median


class BurstTiming:
    def __init__(self, period=20.0, anchor=None):
        self.period = period
        self.anchor = anchor
        self.last_trusted = None
        self.intervals = deque(maxlen=9)
        self.phase_error_ms = None
        self.repeat_adjustment = 0

    def observe(self, observed, repeat_seconds, trusted):
        if not math.isfinite(observed):
            return False
        if self.anchor is None:
            self.anchor = observed
            self.last_trusted = observed if trusted else None
            return True
        cycles = round((observed - self.anchor) / self.period)
        if cycles < 1:
            return False  # Do not learn a 20-second period from repeated copies.
        predicted = self.anchor + cycles * self.period
        residual = observed - predicted
        self.phase_error_ms = residual * 1000
        self.repeat_adjustment = 0
        if abs(residual) > 0.15:
            if trusted:
                self.anchor = observed  # A genuinely reacquired burst phase.
                self.last_trusted = observed
                return True
            return False
        if not trusted:
            return False  # Header-only detections cannot steer an established clock.
        if self.last_trusted is not None:
            delta = observed - self.last_trusted
            periods = round(delta / self.period)
            # A ~33.5 ms copy hop must not be mistaken for clock drift.
            if periods >= 1 and abs(delta - periods * self.period) < 0.015:
                estimate = delta / periods
                if 19.9 <= estimate <= 20.1:
                    self.intervals.append(estimate)
                    self.period = 0.75 * self.period + 0.25 * median(self.intervals)
        self.last_trusted = observed
        if 0.025 <= repeat_seconds <= 0.045:
            self.repeat_adjustment = round(residual / repeat_seconds)
            residual -= self.repeat_adjustment * repeat_seconds
        self.anchor = predicted + 0.25 * residual
        return True

    def snapshot(self):
        return dict(
            period_seconds=self.period,
            rate_samples=len(self.intervals),
            last_phase_error_ms=self.phase_error_ms,
            repeat_adjustment=self.repeat_adjustment,
        )
