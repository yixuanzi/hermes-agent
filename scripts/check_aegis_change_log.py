from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGE_LOG_FILENAME = "hermes_change_log_by_aegis.md"
CHANGE_LOG_PATH = REPO_ROOT / CHANGE_LOG_FILENAME
FILE_HEADER_RE = re.compile(r"^## File:\s*`?([^`\n]+)`?\s*$", flags=re.MULTILINE)
TEST_FILE_PATTERNS = (
    re.compile(r"(^|/)test_[^/]+\.(py|ts|tsx|js|jsx)$"),
    re.compile(r"(^|/)[^/]+_test\.(py|ts|tsx|js|jsx)$"),
    re.compile(r"(^|/)[^/]+\.test\.(ts|tsx|js|jsx)$"),
    re.compile(r"(^|/)[^/]+\.spec\.(ts|tsx|js|jsx)$"),
)


@dataclass
class CheckResult:
    ok: bool
    message: str


def requires_change_log_for_path(path: str) -> bool:
    normalized = path.strip().lstrip("./")
    if not normalized:
        return False
    if normalized == CHANGE_LOG_FILENAME:
        return False
    if normalized.startswith(("aisoc/", "aegis/", "tests/")):
        return False
    if any(pattern.search(normalized) for pattern in TEST_FILE_PATTERNS):
        return False
    suffix = Path(normalized).suffix.lower()
    return suffix == ".py"


def _extract_file_blocks(log_text: str) -> dict[str, str]:
    matches = list(FILE_HEADER_RE.finditer(log_text))
    if not matches:
        return {}

    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(log_text)
        blocks[match.group(1).strip()] = log_text[start:end].strip()
    return blocks


def _block_has_feature_and_intent(block: str) -> bool:
    has_feature = re.search(r"^- Feature:\s*$", block, flags=re.MULTILINE)
    has_intent = re.search(r"^- Intent:\s*$", block, flags=re.MULTILINE)
    return bool(has_feature and has_intent)


def evaluate_change_log_requirement(
    *,
    changed_paths: list[str],
    changed_diff_paths: list[str],
    log_text: str,
) -> CheckResult:
    applicable_paths = sorted({path for path in changed_paths if requires_change_log_for_path(path)})
    if not applicable_paths:
        return CheckResult(
            ok=True,
            message="No Hermes change-log entry required for the current diff.",
        )

    if CHANGE_LOG_FILENAME not in set(changed_diff_paths):
        return CheckResult(
            ok=False,
            message=(
                "Applicable Python code changes detected, but "
                f"`{CHANGE_LOG_FILENAME}` was not updated in the current diff."
            ),
        )

    file_blocks = _extract_file_blocks(log_text)
    if not file_blocks:
        return CheckResult(
            ok=False,
            message="Hermes change-log summary is missing file sections.",
        )

    malformed_files = sorted(
        path for path, block in file_blocks.items() if not _block_has_feature_and_intent(block)
    )
    if malformed_files:
        labels = ", ".join(malformed_files)
        return CheckResult(
            ok=False,
            message=(
                "Hermes change-log summary has file sections without both "
                f"Feature and Intent blocks: {labels}."
            ),
        )

    missing_paths = sorted(path for path in applicable_paths if path not in file_blocks)
    if missing_paths:
        labels = ", ".join(missing_paths)
        return CheckResult(
            ok=False,
            message=f"Missing file blocks for changed Python files: {labels}.",
        )

    return CheckResult(
        ok=True,
        message=(
            "Applicable Python code changes detected and the Hermes change-log "
            "summary covers them."
        ),
    )


def _git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def collect_changed_paths() -> list[str]:
    paths = set(_git_lines("diff", "--name-only"))
    paths.update(_git_lines("diff", "--cached", "--name-only"))
    paths.update(_git_lines("ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def load_change_log_text() -> str:
    if not CHANGE_LOG_PATH.exists():
        return ""
    return CHANGE_LOG_PATH.read_text(encoding="utf-8")


def main() -> int:
    try:
        changed_paths = collect_changed_paths()
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        print(f"FAIL: unable to inspect git diff: {stderr}")
        return 2

    result = evaluate_change_log_requirement(
        changed_paths=changed_paths,
        changed_diff_paths=changed_paths,
        log_text=load_change_log_text(),
    )
    prefix = "PASS" if result.ok else "FAIL"
    print(f"{prefix}: {result.message}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
