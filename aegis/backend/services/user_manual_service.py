"""Safe, read-only access to the Markdown manuals bundled with Aegis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aegis.backend.models import UserManualResponse, UserManualSummary


DEFAULT_MANUAL_ID = "2026-07-27-aegis-core-modules-user-guide"


@dataclass(frozen=True)
class _ManualFile:
    id: str
    title: str
    path: Path


class UserManualService:
    """Enumerate only top-level Markdown files from the bundled docs directory."""

    def __init__(self, docs_dir: Path | None = None) -> None:
        self.docs_dir = docs_dir or Path(__file__).resolve().parent.parent / "docs"

    def list_manuals(self) -> list[UserManualSummary]:
        return [UserManualSummary(id=manual.id, title=manual.title) for manual in self._manual_files()]

    def default_manual_id(self) -> str | None:
        manuals = self._manual_files()
        if not manuals:
            return None
        if any(manual.id == DEFAULT_MANUAL_ID for manual in manuals):
            return DEFAULT_MANUAL_ID
        return manuals[0].id

    def get_manual(self, manual_id: str) -> UserManualResponse | None:
        # Resolve IDs by membership in the server-generated directory, never by
        # concatenating the caller's value into a filesystem path.
        manual = next((item for item in self._manual_files() if item.id == manual_id), None)
        if manual is None:
            return None
        try:
            content = manual.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return UserManualResponse(id=manual.id, title=manual.title, content=content)

    def _manual_files(self) -> list[_ManualFile]:
        try:
            candidates = self.docs_dir.iterdir()
        except OSError:
            return []

        manuals: list[_ManualFile] = []
        for path in candidates:
            if not path.is_file() or path.suffix.lower() != ".md":
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            manuals.append(_ManualFile(id=path.stem, title=self._title_for(path, content), path=path))
        return sorted(manuals, key=lambda manual: (manual.title.casefold(), manual.id))

    @staticmethod
    def _title_for(path: Path, content: str) -> str:
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                if title:
                    return title
        return path.stem.replace("-", " ")
