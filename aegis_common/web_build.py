"""Frontend build shared by the Aegis product consoles (aegis, aisoc, workagent).

Each console serves a Vite bundle from ``<product>/backend/web_dist`` and rebuilds it from
``<product>/frontend`` when the sources are newer. Startup builds through here, and so does
the restart route, which must build while the old process still serves: a build that fails
after the old process has exited leaves nothing running.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import subprocess
import threading

from hermes_constants import find_node_executable

# Everything a Vite build reads; a directory or file a product does not have is skipped.
_SOURCE_DIRS = ("src", "public", "logo")
_SOURCE_FILES = (
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "vite.config.js",
    "tsconfig.json",
    "index.html",
)

# Per npm step. A cold `npm ci` plus `tsc -b && vite build` takes minutes at worst; a step still
# running past this is hung (network, a prompt), and the restart request waiting on it must end.
_STEP_TIMEOUT_SECONDS = 900

# Two builds of one tree (two restart requests) would delete each other's output.
_build_lock = threading.Lock()


class WebBuildError(RuntimeError):
    """A frontend build step failed; ``output`` holds what npm printed."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


def _latest_mtime(root: Path) -> float:
    latest = 0.0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return latest


def web_ui_build_needed(web_dir: Path, dist_dir: Path) -> bool:
    """True when ``web_dir`` has a frontend whose sources are newer than ``dist_dir``."""
    if not (web_dir / "package.json").exists():
        return False
    index_html = dist_dir / "index.html"
    if not index_html.exists():
        return True

    latest_source = max(
        [_latest_mtime(web_dir / name) for name in _SOURCE_DIRS if (web_dir / name).exists()]
        + [(web_dir / name).stat().st_mtime for name in _SOURCE_FILES if (web_dir / name).exists()],
        default=0.0,
    )
    latest_dist = max(index_html.stat().st_mtime, _latest_mtime(dist_dir / "assets"))
    return latest_source > latest_dist


def build_web_ui(
    web_dir: Path,
    dist_dir: Path,
    *,
    label: str,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Build ``web_dir`` into ``dist_dir`` when it is stale; return whether a build ran.

    ``env`` is the environment npm runs with (default: this process's). Raises
    ``WebBuildError`` when npm is missing or a step fails.
    """
    with _build_lock:
        if not web_ui_build_needed(web_dir, dist_dir):
            return False
        npm = find_node_executable("npm")
        if not npm:
            raise WebBuildError(
                f"{label} web UI is not built and npm is not available. Install Node.js, then run "
                f"`cd {web_dir} && npm install && npm run build`."
            )
        install = "ci" if (web_dir / "package-lock.json").exists() else "install"
        for step in ([install, "--silent"], ["run", "build"]):
            command = f"npm {' '.join(step)}"
            try:
                result = subprocess.run(
                    [npm, *step],
                    cwd=web_dir,
                    env=None if env is None else dict(env),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=_STEP_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                raise WebBuildError(
                    f"{label} web UI build failed: `{command}` did not finish within "
                    f"{_STEP_TIMEOUT_SECONDS}s",
                    _joined_output(exc.stdout, exc.stderr),
                ) from exc
            if result.returncode != 0:
                raise WebBuildError(
                    f"{label} web UI build failed: `{command}` exited with {result.returncode}",
                    _joined_output(result.stdout, result.stderr),
                )
        return True


def _joined_output(*streams: str | bytes | None) -> str:
    parts = []
    for stream in streams:
        text = stream.decode("utf-8", "replace") if isinstance(stream, bytes) else stream
        if text and text.strip():
            parts.append(text.rstrip())
    return "\n".join(parts)


__all__ = ["WebBuildError", "build_web_ui", "web_ui_build_needed"]
