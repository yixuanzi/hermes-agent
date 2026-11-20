"""Per-source poll cadence: base interval, idle slow-down, jitter, error backoff, active hours.

Pure timing policy; the adapter owns the loop and the tick. A source never overlaps itself
because the loop awaits each tick before asking for the next delay.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Callable, Optional

_OUTSIDE_HOURS_RECHECK = 900.0


def minutes_until_active(active_hours: Optional[tuple[int, int]], now: dt.datetime) -> int:
    """0 when ``now`` is inside the window (or there is none), else minutes until it opens."""
    if not active_hours:
        return 0
    start, end = active_hours
    minute = now.hour * 60 + now.minute
    inside = start <= minute < end if start <= end else (minute >= start or minute < end)
    return 0 if inside else (start - minute) % 1440


class PollSchedule:
    def __init__(self, *, interval: float, idle_interval: float, idle_after: int, jitter: float,
                 max_backoff: float, active_hours: Optional[tuple[int, int]] = None,
                 rand: Callable[[], float] = random.random):
        self._interval = interval
        self._idle_interval = idle_interval
        self._idle_after = idle_after
        self._jitter = jitter
        self._max_backoff = max_backoff
        self._active_hours = active_hours
        self._rand = rand
        self.idle_streak = 0
        self.errors = 0

    def on_result(self, new_messages: int) -> None:
        self.errors = 0
        self.idle_streak = 0 if new_messages else self.idle_streak + 1

    def on_error(self, kind: str) -> None:
        # A rate limit costs one extra doubling: hammering a throttled endpoint extends the throttle.
        self.errors += 2 if kind == "rate_limit" else 1

    def wait_for_window(self, now: dt.datetime) -> float:
        """Seconds to sleep before polling is allowed again; 0 inside the active window."""
        minutes = minutes_until_active(self._active_hours, now)
        return min(_OUTSIDE_HOURS_RECHECK, minutes * 60.0) if minutes else 0.0

    def next_delay(self) -> float:
        if self.errors:
            delay = min(self._max_backoff, self._interval * (2 ** self.errors))
        elif self.idle_streak >= self._idle_after:
            delay = self._idle_interval
        else:
            delay = self._interval
        return max(1.0, delay * (1 + self._jitter * (2 * self._rand() - 1)))
