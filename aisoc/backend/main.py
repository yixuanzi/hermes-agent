"""Shared AISOC CLI entrypoint for Hermes and direct Python startup."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import sys

from aegis_common.web_build import WebBuildError, build_web_ui, web_ui_build_needed
from hermes_cli._startup_fast import project_root_str


REPO_ROOT = Path(project_root_str())
AISOC_FRONTEND_DIR = REPO_ROOT / "aisoc" / "frontend"
AISOC_DIST_DIR = REPO_ROOT / "aisoc" / "backend" / "web_dist"


def configure_aisoc_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach AISOC arguments to an existing parser."""
    parser.add_argument(
        "-p",
        "--profile",
        help="Direct startup only: Hermes profile to load before launching AISOC",
    )
    parser.add_argument(
        "--module",
        dest="module",
        choices=("server",),
        default="server",
        help="AISOC service module to start (default: server)",
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
        "--skip-build",
        action="store_true",
        help=(
            "Skip the AISOC web UI build step and serve existing dist directly. "
            "Pre-build with: cd aisoc/frontend && npm run build"
        ),
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Build a standalone AISOC parser for direct Python startup."""
    parser = argparse.ArgumentParser(
        prog="aisoc",
        description="Launch the AISOC console for chat operations and runtime controls",
    )
    configure_aisoc_parser(parser)
    parser.set_defaults(func=cmd_aisoc)
    return parser


def _apply_direct_profile_override(argv: list[str] | None = None) -> list[str]:
    """Resolve direct-startup ``-p/--profile`` before AISOC loads Hermes modules."""
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


# Whether this process built its own frontend at startup (no --skip-build, no AISOC_WEB_DIST);
# a restart then builds before stopping, so a broken build leaves this process serving.
_builds_web_ui = False


def _build_web_ui(web_dir: Path = AISOC_FRONTEND_DIR, dist_dir: Path = AISOC_DIST_DIR) -> None:
    """Build the AISOC web UI when sources are newer than the current dist."""
    if not web_ui_build_needed(web_dir, dist_dir):
        return
    print("→ Building aisoc web UI...")
    try:
        build_web_ui(web_dir, dist_dir, label="AISOC")
    except WebBuildError as exc:
        if exc.output:
            print(exc.output)
        raise SystemExit(str(exc)) from exc


def build_web_ui_before_restart(env: Mapping[str, str]) -> None:
    """Build what the replacement would build at startup, while this process still serves.

    Raises ``WebBuildError``; ``env`` is the environment this process was launched with.
    """
    if _builds_web_ui:
        build_web_ui(AISOC_FRONTEND_DIR, AISOC_DIST_DIR, label="AISOC", env=env)


def _ensure_server_dist_available(skip_build: bool) -> None:
    global _builds_web_ui
    dist_root = Path(os.environ["AISOC_WEB_DIST"]) if "AISOC_WEB_DIST" in os.environ else AISOC_DIST_DIR
    if "AISOC_WEB_DIST" not in os.environ and not skip_build:
        _builds_web_ui = True
        _build_web_ui()
        return
    if skip_build and not (dist_root / "index.html").exists():
        print(f"✗ --skip-build was passed but no aisoc web dist found at: {dist_root}")
        print("  Pre-build first:  cd aisoc/frontend && npm install && npm run build")
        print("  Or drop --skip-build to build automatically.")
        raise SystemExit(1)
    if skip_build:
        print(f"→ Skipping aisoc web UI build (--skip-build); using dist at {dist_root}")


def _validate_module_args(args: argparse.Namespace) -> None:
    module = getattr(args, "module", "server") or "server"
    if module != "server":
        print(f"Unsupported AISOC module: {module}", file=sys.stderr)
        raise SystemExit(2)


def cmd_aisoc(args: argparse.Namespace) -> None:
    """Start the AISOC service."""
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

    _ensure_server_dist_available(getattr(args, "skip_build", False))

    from aisoc.backend.server import start_server

    start_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        allow_public=getattr(args, "insecure", False),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the standalone AISOC CLI."""
    effective_argv = _apply_direct_profile_override(argv)
    parser = build_parser()
    args = parser.parse_args(effective_argv)
    func = getattr(args, "func", cmd_aisoc)
    func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
