"""Shared WORKAGENT CLI entrypoint for Hermes and direct Python startup."""

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

from hermes_constants import find_node_executable


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKAGENT_FRONTEND_DIR = REPO_ROOT / "workagent" / "frontend"
WORKAGENT_DIST_DIR = REPO_ROOT / "workagent" / "backend" / "web_dist"


def configure_workagent_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach WORKAGENT arguments to an existing parser."""
    parser.add_argument(
        "-p",
        "--profile",
        help="Direct startup only: Hermes profile to load before launching WORKAGENT",
    )
    parser.add_argument(
        "--module",
        "--model",
        dest="module",
        choices=("server", "a2a", "extcli"),
        default="server",
        help="WORKAGENT service module to start (default: server)",
    )
    parser.add_argument("--port", type=int, default=9120, help="Port (default 9120)")
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
        "--tui",
        action="store_true",
        help=(
            "Expose the in-browser Chat tab (embedded `hermes --tui` via PTY/WebSocket). "
            "Alternatively set HERMES_WORKAGENT_TUI=1."
        ),
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help=(
            "Skip the WORKAGENT web UI build step and serve existing dist directly. "
            "Pre-build with: cd workagent/frontend && npm run build"
        ),
    )
    parser.add_argument("--name", help="A2A module: override AgentCard name")
    parser.add_argument(
        "--description",
        help="A2A module: override AgentCard description",
    )
    parser.add_argument("--card", help="A2A module: load AgentCard JSON from a file")
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="A2A module: reserve streaming capability flag",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="A2A module: maximum worker count (default 4)",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Build a standalone WORKAGENT parser for direct Python startup."""
    parser = argparse.ArgumentParser(
        prog="workagent",
        description="Launch the WORKAGENT console for chat operations and runtime controls",
    )
    configure_workagent_parser(parser)
    parser.set_defaults(func=cmd_workagent)
    return parser


def _apply_direct_profile_override(argv: list[str] | None = None) -> list[str]:
    """Resolve direct-startup ``-p/--profile`` before WORKAGENT loads Hermes modules."""
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
    """Return True when the WORKAGENT frontend should be rebuilt."""
    index_html = dist_dir / "index.html"
    if not index_html.exists():
        return True

    latest_source = 0.0
    for name in ("src", "public"):
        source_dir = web_dir / name
        if source_dir.exists():
            latest_source = max(latest_source, _collect_latest_mtime(source_dir))

    for name in ("package.json", "package-lock.json", "vite.config.ts", "vite.config.js", "tsconfig.json"):
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


def _build_web_ui(web_dir: Path = WORKAGENT_FRONTEND_DIR, dist_dir: Path = WORKAGENT_DIST_DIR) -> None:
    """Build the WORKAGENT web UI when sources are newer than the current dist."""
    if not (web_dir / "package.json").exists():
        return
    if not _web_ui_build_needed(web_dir, dist_dir):
        return

    npm = find_node_executable("npm")
    if not npm:
        raise SystemExit(
            "WORKAGENT web UI is not built and npm is not available. "
            "Install Node.js, then run `cd workagent/frontend && npm install && npm run build`."
        )

    print("→ Building workagent web UI...")
    install_cmd = [npm, "ci", "--silent"] if (web_dir / "package-lock.json").exists() else [npm, "install", "--silent"]
    _run_build_step(install_cmd, web_dir)
    _run_build_step([npm, "run", "build"], web_dir)


def _ensure_server_dist_available(skip_build: bool) -> None:
    dist_root = Path(os.environ["WORKAGENT_WEB_DIST"]) if "WORKAGENT_WEB_DIST" in os.environ else WORKAGENT_DIST_DIR
    if "WORKAGENT_WEB_DIST" not in os.environ and not skip_build:
        _build_web_ui()
        return
    if skip_build and not (dist_root / "index.html").exists():
        print(f"✗ --skip-build was passed but no workagent web dist found at: {dist_root}")
        print("  Pre-build first:  cd workagent/frontend && npm install && npm run build")
        print("  Or drop --skip-build to build automatically.")
        raise SystemExit(1)
    if skip_build:
        print(f"→ Skipping workagent web UI build (--skip-build); using dist at {dist_root}")


def _validate_module_args(args: argparse.Namespace) -> None:
    module = getattr(args, "module", "server") or "server"
    if module == "a2a":
        invalid_flags = []
        if getattr(args, "tui", False):
            invalid_flags.append("--tui")
        if getattr(args, "skip_build", False):
            invalid_flags.append("--skip-build")
        if getattr(args, "no_open", False):
            invalid_flags.append("--no-open")
        if invalid_flags:
            print(
                "A2A module does not support: " + ", ".join(invalid_flags),
                file=sys.stderr,
            )
            raise SystemExit(2)
        return

    if module == "extcli":
        invalid_flags = []
        if getattr(args, "tui", False):
            invalid_flags.append("--tui")
        if getattr(args, "skip_build", False):
            invalid_flags.append("--skip-build")
        if getattr(args, "no_open", False):
            invalid_flags.append("--no-open")
        if getattr(args, "name", None):
            invalid_flags.append("--name")
        if getattr(args, "description", None):
            invalid_flags.append("--description")
        if getattr(args, "card", None):
            invalid_flags.append("--card")
        if getattr(args, "streaming", False):
            invalid_flags.append("--streaming")
        if getattr(args, "workers", 4) != 4:
            invalid_flags.append("--workers")
        if invalid_flags:
            print(
                "extcli module does not support: " + ", ".join(invalid_flags),
                file=sys.stderr,
            )
            raise SystemExit(2)
        return

    invalid_flags = []
    if getattr(args, "name", None):
        invalid_flags.append("--name")
    if getattr(args, "description", None):
        invalid_flags.append("--description")
    if getattr(args, "card", None):
        invalid_flags.append("--card")
    if getattr(args, "streaming", False):
        invalid_flags.append("--streaming")
    if getattr(args, "workers", 4) != 4:
        invalid_flags.append("--workers")
    if invalid_flags:
        print(
            "Server module does not support: " + ", ".join(invalid_flags),
            file=sys.stderr,
        )
        raise SystemExit(2)


def cmd_workagent(args: argparse.Namespace) -> None:
    """Start the WORKAGENT service."""
    _validate_module_args(args)

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

    module = getattr(args, "module", "server") or "server"
    if module == "a2a":
        from workagent.backend.a2a_server import start_a2a_server

        start_a2a_server(
            host=args.host,
            port=args.port,
            allow_public=getattr(args, "insecure", False),
            name=getattr(args, "name", None),
            description=getattr(args, "description", None),
            card_path=getattr(args, "card", None),
            streaming=getattr(args, "streaming", False),
            workers=getattr(args, "workers", 4),
        )
        return

    if module == "extcli":
        from workagent.backend.extcli import start_extcli

        start_extcli()
        return

    _ensure_server_dist_available(getattr(args, "skip_build", False))

    from workagent.backend.server import start_server

    embedded_chat = args.tui or os.environ.get("HERMES_WORKAGENT_TUI") == "1"
    start_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        allow_public=getattr(args, "insecure", False),
        embedded_chat=embedded_chat,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the standalone WORKAGENT CLI."""
    effective_argv = _apply_direct_profile_override(argv)
    parser = build_parser()
    args = parser.parse_args(effective_argv)
    func = getattr(args, "func", cmd_workagent)
    func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
