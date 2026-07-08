from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "check_aegis_change_log.py"
    )
    spec = importlib.util.spec_from_file_location("check_aegis_change_log", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_summary_text() -> str:
    return """# Hermes Change Log by Ages

## File: `scripts/check_aegis_change_log.py`
- Feature:
  - Enforce a task-level review gate.
- Intent:
  - Only apply the workflow to Python code outside `aisoc/` and `aegis/`.
"""


def test_requires_change_log_for_non_aisoc_non_aegis_code_changes():
    mod = _load_script_module()

    result = mod.evaluate_change_log_requirement(
        changed_paths=["scripts/check_aegis_change_log.py"],
        changed_diff_paths=[],
        log_text=_valid_summary_text(),
    )

    assert result.ok is False
    assert "hermes_change_log_by_aegis.md" in result.message


def test_skips_change_log_when_only_aisoc_aegis_or_tests_change():
    mod = _load_script_module()

    result = mod.evaluate_change_log_requirement(
        changed_paths=[
            "aisoc/backend/app.py",
            "aegis/backend/server.py",
            "tests/scripts/test_check_aegis_change_log.py",
            "tools/foo.test.ts",
        ],
        changed_diff_paths=[],
        log_text="",
    )

    assert result.ok is True
    assert "No Hermes change-log entry required" in result.message


def test_skips_change_log_for_non_python_changes():
    mod = _load_script_module()

    result = mod.evaluate_change_log_requirement(
        changed_paths=["AGENTS.md", "docs/superpowers/specs/example.md", "scripts/run_tests.sh"],
        changed_diff_paths=[],
        log_text="",
    )

    assert result.ok is True
    assert "No Hermes change-log entry required" in result.message


def test_rejects_summary_missing_file_sections():
    mod = _load_script_module()

    result = mod.evaluate_change_log_requirement(
        changed_paths=["cli.py"],
        changed_diff_paths=["hermes_change_log_by_aegis.md"],
        log_text="""# Hermes Change Log by Ages
""",
    )

    assert result.ok is False
    assert "missing file sections" in result.message


def test_rejects_when_summary_omits_changed_python_file():
    mod = _load_script_module()

    result = mod.evaluate_change_log_requirement(
        changed_paths=["cli.py"],
        changed_diff_paths=["hermes_change_log_by_aegis.md"],
        log_text=_valid_summary_text(),
    )

    assert result.ok is False
    assert "Missing file blocks for changed Python files" in result.message


def test_accepts_summary_when_log_is_updated():
    mod = _load_script_module()

    result = mod.evaluate_change_log_requirement(
        changed_paths=["cli.py"],
        changed_diff_paths=["hermes_change_log_by_aegis.md", "cli.py"],
        log_text="""# Hermes Change Log by Ages

## File: `scripts/check_aegis_change_log.py`
- Feature:
  - Maintain the workflow checker implementation.
- Intent:
  - Keep the summary file itself represented for future changes.

## File: `cli.py`
- Feature:
  - Add workflow review prompt output.
- Intent:
  - Make completion decisions explicit for CLI tasks.
""",
    )

    assert result.ok is True
    assert "summary covers them" in result.message


def test_rejects_file_block_missing_intent():
    mod = _load_script_module()

    result = mod.evaluate_change_log_requirement(
        changed_paths=["cli.py"],
        changed_diff_paths=["hermes_change_log_by_aegis.md", "cli.py"],
        log_text="""# Hermes Change Log by Ages

## File: `cli.py`
- Feature:
  - Add workflow review prompt output.
""",
    )

    assert result.ok is False
    assert "without both Feature and Intent" in result.message


def test_classifies_test_like_paths():
    mod = _load_script_module()

    assert mod.requires_change_log_for_path("cli.py") is True
    assert mod.requires_change_log_for_path("foo_test.py") is False
    assert mod.requires_change_log_for_path("test_foo.py") is False
    assert mod.requires_change_log_for_path("scripts/run_tests.sh") is False
    assert mod.requires_change_log_for_path("ui-tui/src/app.tsx") is False
    assert mod.requires_change_log_for_path("AGENTS.md") is False
    assert mod.requires_change_log_for_path("tests/cli/test_cli.py") is False
    assert mod.requires_change_log_for_path("ui-tui/src/foo.test.ts") is False
    assert mod.requires_change_log_for_path("aegis/backend/app.py") is False
    assert mod.requires_change_log_for_path("aisoc/backend/app.py") is False
