# Hermes Change Log by Ages

Use this file as a file-centric summary of modified Python files when a task
changes non-test `.py` code outside `aisoc/` and `aegis/`.

This file is not a chronological task diary. Keep it organized by file so a
future agent can quickly see which files have changed, which feature points
were touched, and why those changes exist.

Required summary structure:

- one or more `## File: \`path\`` blocks
- at least one `Feature` and `Intent` pair inside each file block
- multiple `Feature` / `Intent` pairs are allowed for the same file

Template:

```md
## File: `path/to/file.py`
- Feature:
  - What changed in this file.
- Intent:
  - Why this change was made.
- Feature:
  - Another change point in the same file.
- Intent:
  - Why that change point exists.

## File: `another/file.py`
- Feature:
  - Another file-level summary point.
- Intent:
  - Intent for that file-level point.
```

## File: `AGENTS.md`
- Feature:
  - Define the trigger rule for when the Hermes change-log workflow applies.
- Intent:
  - Make the guardrail explicit in the main repository guide so future work can
    consistently decide when a `.py` change must be summarized.
- Feature:
  - Require the summary file to stay file-centric instead of chronological.
- Intent:
  - Avoid a running task journal and keep the artifact useful as a durable
    per-file summary.

## File: `hermes_change_log_by_aegis.md`
- Feature:
  - Rename the change-log artifact and make its title `Hermes Change Log by Ages`.
- Intent:
  - Give the repository a durable summary document with a stable, descriptive
    identity.
- Feature:
  - Replace the old task-entry template with file-based summary blocks.
- Intent:
  - Ensure the log records modified files, their feature points, and intent
    rather than appending timeline-style entries.

## File: `scripts/check_aegis_change_log.py`
- Feature:
  - Restrict enforcement to non-test `.py` files outside `aisoc/`, `aegis/`,
    and `tests/`.
- Intent:
  - Keep the workflow focused on Python code paths where durable change intent
    matters most.
- Feature:
  - Validate that the summary file contains a block for every applicable Python
    file in the current diff.
- Intent:
  - Make sure the log acts as a true per-file summary rather than a task note
    that can omit touched files.
- Feature:
  - Require each file block to include both `Feature` and `Intent`.
- Intent:
  - Guarantee that every summarized file explains both what changed and why.

## File: `tests/scripts/test_check_aegis_change_log.py`
- Feature:
  - Cover the `.py`-only trigger rule and file-summary validation behavior.
- Intent:
  - Keep the checker aligned with the documented workflow and catch regressions
    when the format evolves.
