"""Durable poll state: per-source watermarks, handled message ids, review drafts.

One JSON file per Lark account under the profile's home, so a restart neither replays handled
messages nor loses pending drafts. Message ids are the source of truth for "already handled": the
CLI renders ``create_time`` to the minute, so watermarks only narrow the search window.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_MAX_SEEN = 5000


def state_path(open_id: str) -> Path:
    return get_hermes_home() / "lark_user" / f"{open_id}.json"


class CursorStore:
    def __init__(self, path: Path, *, seen_ttl: float):
        self._path = path
        self._seen_ttl = seen_ttl
        self.watermarks: dict[str, float] = {}
        self.seen: dict[str, float] = {}
        self.drafts: dict[str, dict[str, Any]] = {}
        self.review_chat: str = ""
        self.next_draft: int = 1

    def load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("lark_user: unreadable state file %s (%s); starting fresh", self._path.name, exc)
            return
        self.watermarks = {str(k): float(v) for k, v in (data.get("watermarks") or {}).items()}
        self.seen = {str(k): float(v) for k, v in (data.get("seen") or {}).items()}
        self.drafts = {str(k): v for k, v in (data.get("drafts") or {}).items() if isinstance(v, dict)}
        self.review_chat = str(data.get("review_chat") or "")
        self.next_draft = int(data.get("next_draft") or 1)

    def save(self) -> None:
        self._prune()
        payload = {"watermarks": self.watermarks, "seen": self.seen, "drafts": self.drafts,
                   "review_chat": self.review_chat, "next_draft": self.next_draft}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".state-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, self._path)

    def is_seen(self, message_id: str) -> bool:
        return message_id in self.seen

    def mark_seen(self, message_id: str, *, now: Optional[float] = None) -> None:
        self.seen[message_id] = now if now is not None else time.time()

    def advance(self, source: str, created_at: float) -> None:
        if created_at > self.watermarks.get(source, 0.0):
            self.watermarks[source] = created_at

    def _prune(self) -> None:
        cutoff = time.time() - self._seen_ttl
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
        if len(self.seen) > _MAX_SEEN:
            keep = sorted(self.seen.items(), key=lambda kv: kv[1])[-_MAX_SEEN:]
            self.seen = dict(keep)
