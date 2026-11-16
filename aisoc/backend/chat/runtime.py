"""Runtime adapters for AISOC chat + delegate foreground handoff."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import threading
import time


class ChatInputAdapter:
    def __init__(
        self,
        *,
        on_enter_foreground: Callable[[ChatInputAdapter], bool] | None = None,
        on_exit_foreground: Callable[[ChatInputAdapter], None] | None = None,
    ) -> None:
        self._condition = threading.Condition()
        self._lines: deque[str] = deque()
        self._closed = False
        self._waiting_for_input = False
        self._last_read_timed_out = False
        self._on_enter_foreground = on_enter_foreground
        self._on_exit_foreground = on_exit_foreground

    def enter_foreground(self) -> bool:
        if self._on_enter_foreground is None:
            return True
        return bool(self._on_enter_foreground(self))

    def exit_foreground(self) -> None:
        if self._on_exit_foreground is not None:
            self._on_exit_foreground(self)

    def push_line(self, text: str) -> bool:
        with self._condition:
            if self._closed:
                return False
            self._waiting_for_input = False
            self._lines.append(text)
            self._condition.notify_all()
            return True

    def read_line(self, timeout: float | None = None) -> str | None:
        with self._condition:
            self._last_read_timed_out = False
            deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
            while not self._lines and not self._closed:
                self._waiting_for_input = True
                self._condition.notify_all()
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._waiting_for_input = False
                    self._last_read_timed_out = True
                    self._condition.notify_all()
                    return None
                self._condition.wait(timeout=remaining)
            self._waiting_for_input = False
            if self._lines:
                line = self._lines.popleft()
                self._condition.notify_all()
                return line
            return None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._waiting_for_input = False
            self._condition.notify_all()

    def is_waiting_for_input(self) -> bool:
        with self._condition:
            return self._waiting_for_input

    def last_read_timed_out(self) -> bool:
        with self._condition:
            return self._last_read_timed_out


class ChatOutputAdapter:
    def __init__(self, actor) -> None:
        self._actor = actor

    def emit(
        self,
        source: str,
        event_type: str,
        content: str,
        *,
        session_id: str | None = None,
    ) -> None:
        self._actor.handle_delegate_output(
            source=source,
            event_type=event_type,
            content=content,
            delegate_session_id=session_id,
        )
