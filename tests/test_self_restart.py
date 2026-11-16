"""Behavior tests for the shared process self-restart helper."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import json
import time
import tomllib

import pytest


@pytest.mark.parametrize(
    ("orig_argv", "expected"),
    [
        (
            ["/opt/hermes/bin/hermes", "aisoc", "--port", "9120"],
            ["/opt/hermes/bin/hermes", "aisoc", "--port", "9120"],
        ),
        (
            [sys.executable, "aisoc/backend/main.py", "--port", "9120"],
            [sys.executable, "aisoc/backend/main.py", "--port", "9120"],
        ),
        (
            [sys.executable, "-m", "aisoc.backend.main", "--port", "9120"],
            [sys.executable, "-m", "aisoc.backend.main", "--port", "9120"],
        ),
    ],
)
def test_reconstruct_startup_argv_preserves_supported_launch_forms(
    orig_argv: list[str], expected: list[str]
) -> None:
    from hermes_self_restart import reconstruct_startup_argv

    assert reconstruct_startup_argv(orig_argv=orig_argv) == expected


def test_duplicate_request_does_not_launch_a_second_watcher(monkeypatch) -> None:
    import hermes_self_restart as self_restart

    launched = []
    shutdown_services = []
    monkeypatch.setattr(self_restart, "_restart_requested", False, raising=False)
    monkeypatch.setattr(
        self_restart,
        "_start_detached_watcher",
        lambda spec: launched.append(spec),
        raising=False,
    )

    first = self_restart.request_self_restart(
        "aisoc", lambda: shutdown_services.append("aisoc")
    )
    duplicate = self_restart.request_self_restart(
        "aegis", lambda: shutdown_services.append("aegis")
    )

    assert first.as_dict() == {
        "accepted": True,
        "already_requested": False,
        "service": "aisoc",
        "pid": os.getpid(),
    }
    assert duplicate.as_dict() == {
        "accepted": False,
        "already_requested": True,
        "service": "aegis",
        "pid": os.getpid(),
    }
    assert len(launched) == 1
    assert shutdown_services == ["aisoc"]


def test_detached_watcher_receives_startup_state_and_normal_environment(
    monkeypatch, tmp_path: Path
) -> None:
    import hermes_self_restart as self_restart

    popen_calls = []
    monkeypatch.setattr(
        self_restart.subprocess,
        "Popen",
        lambda command, **kwargs: popen_calls.append((command, kwargs)),
    )
    spec = self_restart._RestartSpec(
        argv=(sys.executable, "service.py", "--flag"),
        cwd=str(tmp_path),
        env={"PATH": "/test/bin", "NORMAL_SETTING": "present"},
        old_pid=321,
        old_create_time=1234.5,
    )

    self_restart._start_detached_watcher(spec)

    assert len(popen_calls) == 1
    command, kwargs = popen_calls[0]
    assert command[:2] == [sys.executable, str(Path(self_restart.__file__).resolve())]
    assert command[2] == "--watch"
    decoded = self_restart._decode_watcher_payload(command[3])
    assert decoded.argv == spec.argv
    assert decoded.cwd == spec.cwd
    assert decoded.old_pid == spec.old_pid
    assert decoded.old_create_time == spec.old_create_time
    assert kwargs["env"] == spec.env
    assert not any(key.startswith("HERMES_RESTART") for key in kwargs["env"])
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.DETACHED_PROCESS
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert kwargs["start_new_session"] is True


def test_watcher_does_not_wait_on_a_reused_pid(monkeypatch) -> None:
    import hermes_self_restart as self_restart

    class ReusedProcess:
        def create_time(self) -> float:
            return 200.0

        def is_running(self) -> bool:
            raise AssertionError("reused process must not be polled")

    monkeypatch.setattr(self_restart.psutil, "Process", lambda _pid: ReusedProcess())

    self_restart._wait_for_old_process(old_pid=321, old_create_time=100.0)


def test_watcher_timeout_does_not_launch_replacement(monkeypatch, tmp_path: Path) -> None:
    import hermes_self_restart as self_restart

    class InaccessibleProcess:
        def create_time(self) -> float:
            return 100.0

        def is_running(self) -> bool:
            raise self_restart.psutil.AccessDenied(321)

    clock = [0.0]
    launched = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        self_restart.psutil, "Process", lambda _pid: InaccessibleProcess()
    )
    monkeypatch.setattr(self_restart.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        self_restart.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(
        self_restart.subprocess,
        "Popen",
        lambda command, **kwargs: launched.append((command, kwargs)),
    )
    spec = self_restart._RestartSpec(
        argv=(sys.executable, "service.py", "--token", "do-not-log-this"),
        cwd=str(tmp_path),
        env={"PATH": "/test/bin", "SECRET_TOKEN": "do-not-log-this"},
        old_pid=321,
        old_create_time=100.0,
    )

    self_restart._run_watcher(spec, wait_timeout=0.1)

    assert launched == []
    diagnostic = (tmp_path / "logs" / "self-restart.log").read_text("utf-8")
    assert "event=watch_timeout" in diagnostic
    assert "old_pid=321" in diagnostic
    assert "timeout=0.1" in diagnostic
    assert "do-not-log-this" not in diagnostic


def test_replacement_launch_failure_writes_durable_diagnostic(
    monkeypatch, tmp_path: Path
) -> None:
    import hermes_self_restart as self_restart

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        self_restart, "_wait_for_old_process", lambda **_kwargs: True
    )
    monkeypatch.setattr(
        self_restart.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FileNotFoundError(2, "missing executable")
        ),
    )
    spec = self_restart._RestartSpec(
        argv=("missing-service", "--token", "do-not-log-this"),
        cwd=str(tmp_path),
        env={"SECRET_TOKEN": "do-not-log-this"},
        old_pid=321,
        old_create_time=100.0,
    )

    self_restart._run_watcher(spec, wait_timeout=0.1)

    diagnostic = (tmp_path / "logs" / "self-restart.log").read_text("utf-8")
    assert "replacement launch failed" in diagnostic
    assert "FileNotFoundError" in diagnostic
    assert "errno=2" in diagnostic
    assert "do-not-log-this" not in diagnostic


def test_subprocess_exits_and_restarts_the_same_command(tmp_path: Path) -> None:
    state_path = tmp_path / "restart-state.json"
    service_path = tmp_path / "restartable_service.py"
    service_path.write_text(
        """
import json
import os
from pathlib import Path
import sys

state_path = Path(sys.argv[1])
record = {
    "argv": sys.orig_argv,
    "cwd": os.getcwd(),
    "normal_setting": os.environ.get("SMOKE_NORMAL_SETTING"),
    "pid": os.getpid(),
}
if not state_path.exists():
    record["stage"] = "old"
    state_path.write_text(json.dumps(record), encoding="utf-8")
    from hermes_self_restart import request_self_restart
    request_self_restart("smoke", lambda: sys.exit(0))
    raise AssertionError("graceful shutdown callback did not exit")

record["stage"] = "restarted"
state_path.write_text(json.dumps(record), encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )
    command = [sys.executable, str(service_path), str(state_path), "same-argument"]
    env = dict(os.environ)
    env["SMOKE_NORMAL_SETTING"] = "preserved"
    project_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (project_root, env.get("PYTHONPATH", "")) if part
    )
    old_process = subprocess.Popen(command, cwd=tmp_path, env=env)
    smoke_timeout = 10.0

    try:
        assert old_process.wait(timeout=smoke_timeout) == 0
        deadline = time.monotonic() + smoke_timeout
        restarted = None
        while time.monotonic() < deadline:
            try:
                candidate = json.loads(state_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                candidate = None
            if candidate and candidate.get("stage") == "restarted":
                restarted = candidate
                break
            time.sleep(0.05)
    finally:
        if old_process.poll() is None:
            old_process.terminate()
            old_process.wait(timeout=3)

    assert restarted is not None, "replacement process did not write its state"
    assert restarted["argv"] == command
    assert restarted["cwd"] == str(tmp_path)
    assert restarted["normal_setting"] == "preserved"
    assert restarted["pid"] != old_process.pid


def test_shared_restart_module_is_in_the_installed_distribution() -> None:
    project_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text("utf-8"))

    assert "hermes_self_restart" in metadata["tool"]["setuptools"]["py-modules"]
