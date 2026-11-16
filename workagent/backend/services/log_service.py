"""Logs service adapters."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from hermes_constants import get_hermes_home


def read_logs(
    *,
    file: str = "agent",
    lines: int = 100,
    level: str | None = None,
    component: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    from hermes_cli.logs import _read_tail, LOG_FILES

    log_name = LOG_FILES.get(file)
    if not log_name:
        raise HTTPException(status_code=400, detail=f"Unknown log file: {file}")
    log_path = get_hermes_home() / "logs" / log_name
    if not log_path.exists():
        return {"file": file, "lines": []}

    try:
        from hermes_logging import COMPONENT_PREFIXES
    except Exception:
        COMPONENT_PREFIXES = {}

    min_level = level if level and level.upper() != "ALL" else None
    if component and component.lower() != "all":
        comp_prefixes = COMPONENT_PREFIXES.get(component)
        if comp_prefixes is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown component: {component}. Available: "
                    f"{', '.join(sorted(COMPONENT_PREFIXES))}"
                ),
            )
    else:
        comp_prefixes = None

    has_filters = bool(min_level or comp_prefixes or search)
    result = _read_tail(
        log_path,
        min(lines, 500) if not search else 2000,
        has_filters=has_filters,
        min_level=min_level,
        component_prefixes=comp_prefixes,
    )
    if search:
        needle = search.lower()
        result = [line for line in result if needle in line.lower()][-min(lines, 500) :]
    return {"file": file, "lines": result}

