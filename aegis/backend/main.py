"""Shared Aegis CLI entrypoint for Hermes and direct Python startup."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from hermes_cli._startup_fast import project_root_str
from hermes_constants import find_node_executable


REPO_ROOT = Path(project_root_str())
AEGIS_FRONTEND_DIR = REPO_ROOT / "aegis" / "frontend"
AEGIS_DIST_DIR = REPO_ROOT / "aegis" / "backend" / "web_dist"


def configure_aegis_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach Aegis arguments to an existing parser."""
    parser.add_argument(
        "-p",
        "--profile",
        help="Direct startup only: Hermes profile to load before launching Aegis",
    )
    parser.add_argument("--port", type=int, default=9130, help="Port (default 9130)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default 127.0.0.1)")
    parser.add_argument(
        "--no-open", action="store_true", help="Don't open browser automatically"
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Allow binding to non-localhost (DANGEROUS: exposes APIs on the network)",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help=(
            "Skip the Aegis web UI build step and serve existing dist directly. "
            "Pre-build with: cd aegis/frontend && npm run build"
        ),
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop all running hermes aegis processes and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="List running hermes aegis processes and exit",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Build a standalone Aegis parser for direct Python startup."""
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="Launch the Aegis console for agent orchestration and routing policy controls",
    )
    configure_aegis_parser(parser)
    parser.set_defaults(func=cmd_aegis)
    return parser


def _apply_direct_profile_override(argv: list[str] | None = None) -> list[str]:
    """Resolve direct-startup ``-p/--profile`` before Aegis loads Hermes modules."""
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    profile_name: str | None = None
    consume = 0
    consume_at = -1

    for index, arg in enumerate(effective_argv):
        if arg in {"--profile", "-p"} and index + 1 < len(effective_argv):
            profile_name = effective_argv[index + 1]
            consume = 2
            consume_at = index
            break
        if arg.startswith("--profile="):
            profile_name = arg.split("=", 1)[1]
            consume = 1
            consume_at = index
            break

    if profile_name is None:
        return effective_argv

    try:
        from hermes_cli.profiles import resolve_profile_env

        os.environ["HERMES_HOME"] = resolve_profile_env(profile_name)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if consume <= 0 or consume_at < 0:
        return effective_argv
    return effective_argv[:consume_at] + effective_argv[consume_at + consume :]


def _find_stale_aegis_pids() -> list[int]:
    """Return running Aegis service PIDs other than ourselves."""
    patterns = [
        "hermes aegis",
        "hermes_cli.main aegis",
        "hermes_cli/main.py aegis",
        "aegis/backend/main.py",
        "aegis.backend.main",
    ]
    self_pid = os.getpid()
    aegis_pids: list[int] = []

    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                stripped = line.strip()
                if not stripped or "grep" in stripped:
                    continue
                parts = stripped.split(None, 1)
                if len(parts) != 2:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                command = parts[1]
                if any(pattern in command for pattern in patterns) and pid != self_pid:
                    aegis_pids.append(pid)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []

    return aegis_pids


def _pid_exists(pid: int) -> bool:
    """Return True when a process still exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_stale_aegis_processes() -> None:
    """Kill running Aegis processes launched through Hermes or backend main."""
    pids = _find_stale_aegis_pids()
    if not pids:
        return

    print(f"⟲ Stopping {len(pids)} aegis process(es)")

    import signal
    import time

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            pass

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not any(_pid_exists(pid) for pid in pids):
            return
        time.sleep(0.1)

    for pid in pids:
        try:
            if _pid_exists(pid):
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _collect_latest_mtime(root: Path) -> float:
    latest = 0.0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return latest


def _web_ui_build_needed(web_dir: Path, dist_dir: Path) -> bool:
    """Return True when the Aegis frontend should be rebuilt."""
    index_html = dist_dir / "index.html"
    if not index_html.exists():
        return True

    latest_source = 0.0
    for name in ("src", "public", "logo"):
        source_dir = web_dir / name
        if source_dir.exists():
            latest_source = max(latest_source, _collect_latest_mtime(source_dir))

    for name in ("package.json", "package-lock.json", "vite.config.ts", "vite.config.js", "tsconfig.json", "index.html"):
        candidate = web_dir / name
        if candidate.exists():
            latest_source = max(latest_source, candidate.stat().st_mtime)

    try:
        latest_dist = max(index_html.stat().st_mtime, _collect_latest_mtime(dist_dir / "assets"))
    except OSError:
        latest_dist = index_html.stat().st_mtime

    return latest_source > latest_dist


def _run_build_step(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode == 0:
        return

    for blob in (result.stdout, result.stderr):
        text = (blob or "").rstrip()
        if text:
            print(text)
    raise SystemExit(result.returncode)


def _build_web_ui(web_dir: Path = AEGIS_FRONTEND_DIR, dist_dir: Path = AEGIS_DIST_DIR) -> None:
    """Build the Aegis web UI when sources are newer than the current dist."""
    if not (web_dir / "package.json").exists():
        return
    if not _web_ui_build_needed(web_dir, dist_dir):
        return

    npm = find_node_executable("npm")
    if not npm:
        raise SystemExit(
            "Aegis web UI is not built and npm is not available. "
            "Install Node.js, then run `cd aegis/frontend && npm install && npm run build`."
        )

    print("→ Building aegis web UI...")
    install_cmd = [npm, "ci", "--silent"] if (web_dir / "package-lock.json").exists() else [npm, "install", "--silent"]
    _run_build_step(install_cmd, web_dir)
    _run_build_step([npm, "run", "build"], web_dir)


def _ensure_server_dist_available(skip_build: bool) -> None:
    dist_root = Path(os.environ["AEGIS_WEB_DIST"]) if "AEGIS_WEB_DIST" in os.environ else AEGIS_DIST_DIR
    if "AEGIS_WEB_DIST" not in os.environ and not skip_build:
        _build_web_ui()
        return
    if skip_build and not (dist_root / "index.html").exists():
        print(f"✗ --skip-build was passed but no aegis web dist found at: {dist_root}")
        print("  Pre-build first:  cd aegis/frontend && npm install && npm run build")
        print("  Or drop --skip-build to build automatically.")
        raise SystemExit(1)
    if skip_build:
        print(f"→ Skipping aegis web UI build (--skip-build); using dist at {dist_root}")


def cmd_aegis(args: argparse.Namespace) -> None:
    """Start the Aegis service, or manage running Aegis processes."""
    if getattr(args, "status", False):
        pids = _find_stale_aegis_pids()
        if not pids:
            print("No hermes aegis processes running.")
            raise SystemExit(0)
        print(f"{len(pids)} hermes aegis process(es) running:")
        for pid in pids:
            print(f"    PID {pid}")
        raise SystemExit(0)

    if getattr(args, "stop", False):
        pids = _find_stale_aegis_pids()
        if not pids:
            print("No hermes aegis processes running.")
            raise SystemExit(0)
        _kill_stale_aegis_processes()
        raise SystemExit(0 if not _find_stale_aegis_pids() else 1)

    try:
        import fastapi
        import uvicorn
    except ImportError as exc:
        print("Web UI dependencies not installed (need fastapi + uvicorn).")
        print(
            "Re-install the package into this interpreter so metadata updates apply:\n"
            f"  cd {REPO_ROOT}\n"
            f"  {sys.executable} -m pip install -e ."
        )
        print(f"Import error: {exc}")
        raise SystemExit(1)

    _ensure_server_dist_available(getattr(args, "skip_build", False))

    from aegis.backend.server import start_server

    start_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        allow_public=getattr(args, "insecure", False),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the standalone Aegis CLI."""
    effective_argv = _apply_direct_profile_override(argv)
    parser = build_parser()
    args = parser.parse_args(effective_argv)
    func = getattr(args, "func", cmd_aegis)
    func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
