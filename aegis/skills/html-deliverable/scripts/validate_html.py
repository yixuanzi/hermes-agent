#!/usr/bin/env python3
"""Validate the minimum delivery contract for one generated HTML artifact."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*[A-Z0-9_ -]+\s*\}\}")


class DocumentInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.title_text: list[str] = []
        self.form_submission_attributes: set[str] = set()
        self._inside_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag.lower())
        if tag.lower() == "form":
            self.form_submission_attributes.update(
                name.lower()
                for name, _value in attrs
                if name.lower() in {"action", "method", "target"}
            )
        if tag.lower() == "title":
            self._inside_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self.title_text.append(data)


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists() or not path.is_file():
        return [f"File not found: {path}"]
    if path.suffix.lower() not in {".html", ".htm"}:
        errors.append("Expected an .html or .htm file.")

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["File must be UTF-8 encoded."]

    if not re.search(r"<!doctype\s+html", content, re.IGNORECASE):
        errors.append("Missing <!doctype html> declaration.")
    if PLACEHOLDER_PATTERN.search(content):
        errors.append("Unresolved {{PLACEHOLDER}} token found.")
    inspector = DocumentInspector()
    try:
        inspector.feed(content)
        inspector.close()
    except Exception as exc:  # HTMLParser rarely raises, but keep an actionable result.
        errors.append(f"Could not parse HTML: {exc}")
        return errors

    for required_tag in ("html", "head", "title", "body"):
        if required_tag not in inspector.tags:
            errors.append(f"Missing <{required_tag}> element.")
    if not "".join(inspector.title_text).strip():
        errors.append("Document <title> must not be empty.")
    if inspector.form_submission_attributes:
        errors.append("Forms must not set action, method, or target attributes.")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_html.py <output.html>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    errors = validate(path)
    if errors:
        print(f"Invalid HTML deliverable: {path}", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"HTML deliverable is valid: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
