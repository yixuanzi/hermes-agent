"""Shared process self-restart support for Hermes service applications."""

from __future__ import annotations

import base64
from datetime import datetime, timezone, UTC
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import psutil

from hermes_constants import get_hermes_home


DEFAULT_RESTART_WATCH_TIMEOUT = 60.0


@dataclass(frozen=True)
class RestartResult:
    """Stable response payload returned to service restart routes."""

    accepted: bool
    already_requested: bool
    service: str
    pid: int

    def as_dict(self) -> dict[str, bool | str | int]:
        return asdict(self)


@dataclass(frozen=True)
class _RestartSpec:
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    old_pid: int
    old_create_time: float


_restart_lock = threading.Lock()
_restart_requested = False


def reconstruct_startup_argv(
    *, orig_argv: Sequence[str] | None = None
) -> list[str]:
    """Return the interpreter's original, replayable startup command.

    Python 3.11's ``sys.orig_argv`` retains details which ``sys.argv`` drops,
    most importantly whether a service was launched through ``python -m``.
    Console-script and direct-script launches are preserved by the same API.
    """
    source = sys.orig_argv if orig_argv is None else orig_argv
    command = [str(arg) for arg in source]
    if not command:
        raise RuntimeError("Cannot restart a process with an empty startup argv")
    return command


def _capture_restart_spec() -> _RestartSpec:
    pid = os.getpid()
    return _RestartSpec(
        argv=tuple(reconstruct_startup_argv()),
        cwd=os.getcwd(),
        env=dict(os.environ),
        old_pid=pid,
        old_create_time=psutil.Process(pid).create_time(),
    )


def _start_detached_watcher(spec: _RestartSpec) -> None:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--watch",
        _encode_watcher_payload(spec),
    ]
    popen_kwargs: dict[str, object] = {
        "cwd": spec.cwd,
        "env": spec.env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(command, **popen_kwargs)


def _encode_watcher_payload(spec: _RestartSpec) -> str:
    payload = {
        "argv": list(spec.argv),
        "cwd": spec.cwd,
        "old_pid": spec.old_pid,
        "old_create_time": spec.old_create_time,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_watcher_payload(payload: str) -> _RestartSpec:
    data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    argv = data.get("argv")
    if not isinstance(argv, list) or not argv or not all(
        isinstance(arg, str) for arg in argv
    ):
        raise ValueError("Restart watcher payload has invalid argv")
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("Restart watcher payload has invalid cwd")
    return _RestartSpec(
        argv=tuple(argv),
        cwd=cwd,
        env=dict(os.environ),
        old_pid=int(data["old_pid"]),
        old_create_time=float(data["old_create_time"]),
    )


def _wait_for_old_process(
    *,
    old_pid: int,
    old_create_time: float,
    timeout: float = DEFAULT_RESTART_WATCH_TIMEOUT,
) -> bool:
    """Wait for one process identity; return whether it exited before timeout."""
    try:
        process = psutil.Process(old_pid)
        if abs(process.create_time() - old_create_time) > 0.001:
            return True
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return True

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        try:
            if abs(process.create_time() - old_create_time) > 0.001:
                return True
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return True
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return True
        except psutil.AccessDenied:
            # The current process is normally inspectable. If a platform
            # transiently denies status access, retain the PID identity and
            # retry instead of racing the replacement against the old server.
            pass
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return False


def _run_watcher(
    spec: _RestartSpec,
    *,
    wait_timeout: float = DEFAULT_RESTART_WATCH_TIMEOUT,
) -> None:
    exited = _wait_for_old_process(
        old_pid=spec.old_pid,
        old_create_time=spec.old_create_time,
        timeout=wait_timeout,
    )
    if not exited:
        _write_restart_timeout_diagnostic(spec, wait_timeout)
        return
    try:
        subprocess.Popen(
            list(spec.argv),
            cwd=spec.cwd,
            env=spec.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as exc:
        _write_restart_diagnostic(spec, exc)


def _write_restart_diagnostic(spec: _RestartSpec, exc: Exception) -> None:
    """Persist safe replacement failure metadata when detached stdio is closed."""
    del spec  # Never serialize argv, cwd, or environment into diagnostics.
    error_type = type(exc).__name__
    error_number = getattr(exc, "errno", None)
    _append_restart_diagnostic(
        f"event=replacement_launch_failed replacement launch failed: "
        f"{error_type} errno={error_number}"
    )


def _write_restart_timeout_diagnostic(spec: _RestartSpec, timeout: float) -> None:
    """Persist a safe timeout event without serializing process arguments."""
    old_pid = spec.old_pid
    del spec
    _append_restart_diagnostic(
        f"event=watch_timeout old_pid={old_pid} timeout={max(0.0, timeout):g}"
    )


def _append_restart_diagnostic(message: str) -> None:
    """Durably append one pre-sanitized self-restart diagnostic message."""
    try:
        log_dir = get_hermes_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = f"{datetime.now(UTC).isoformat()} {message}\n"
        with (log_dir / "self-restart.log").open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        # This is the last-resort path after detached stdio has been closed.
        # If the state directory itself is unavailable there is nowhere safer
        # to report without risking token-bearing process state.
        return


def _main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--watch":
        return 2
    _run_watcher(_decode_watcher_payload(args[1]))
    return 0


def request_self_restart(
    service: str, graceful_shutdown: Callable[[], None]
) -> RestartResult:
    """Request one detached restart, then begin the caller's graceful exit."""
    global _restart_requested

    pid = os.getpid()
    with _restart_lock:
        if _restart_requested:
            return RestartResult(
                accepted=False,
                already_requested=True,
                service=service,
                pid=pid,
            )

        _restart_requested = True
        try:
            _start_detached_watcher(_capture_restart_spec())
        except BaseException:
            _restart_requested = False
            raise

    graceful_shutdown()
    return RestartResult(
        accepted=True,
        already_requested=False,
        service=service,
        pid=pid,
    )


__all__ = ["RestartResult", "reconstruct_startup_argv", "request_self_restart"]


if __name__ == "__main__":
    raise SystemExit(_main())
