"""Images and files in an inbound message -> cached local files, attached to the turn.

With group context on, the sender's own earlier messages shown in that context count too: a file
sent first and asked about in a later @mention reaches the agent with the mention.

lark-cli renders a message's resources in ``content`` as markers: ``[Image: img_…]`` (an image
message), ``![Image](img_…)`` (an image inside rich text) and ``<file key="file_…" name="…"/>`` (a
file). Each one is downloaded with ``+messages-resources-download`` into the gateway's image or
document cache as ``lark_<key>.<ext | file name>``. A key already in the cache (the same image
mentioned again, a forwarded file, a re-poll) is reused, not downloaded again.

The text is left as it is: the gateway's attachment notes give the agent each file's path (the
agent-visible one, re-homed for a routed profile), so a path written into the text would only
repeat it. Audio, video and stickers are not downloaded. A failed download is skipped and never
fails the turn.
"""

from __future__ import annotations

import asyncio
import html
import logging
import mimetypes
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from gateway.platforms.base import (
    SUPPORTED_DOCUMENT_TYPES, SUPPORTED_IMAGE_DOCUMENT_TYPES, get_document_cache_dir, get_image_cache_dir,
    validate_inbound_media_size,
)

from .lark_user_cli import LarkCli, LarkCliError

logger = logging.getLogger(__name__)

# A key becomes part of a file name, so only key characters match; "." never does, which keeps
# the "lark_<key>." lookup prefix of one key from matching another.
_KEY = r"[A-Za-z0-9_-]+"
_MARKER_RE = re.compile(
    rf"\[Image: (?P<image>{_KEY})\]"
    rf"|!\[Image\]\((?P<post_image>{_KEY})\)"
    rf'|<file key="(?P<file>{_KEY})" name="(?P<name>[^"]*)"\s*/>')
_MAX_NAME_CHARS = 120
_PARALLEL_DOWNLOADS = 3


@dataclass(frozen=True)
class Resource:
    key: str
    kind: str  # lark-cli --type: "image" | "file"
    name: str  # the file's own name; "" for an image

    @property
    def is_image(self) -> bool:
        """Routed to the image cache: an image, or a file whose name says it is one."""
        return self.kind == "image" or Path(self.name).suffix.lower() in SUPPORTED_IMAGE_DOCUMENT_TYPES


@dataclass(frozen=True)
class Attachment:
    path: str
    media_type: str
    name: str


def find_resources(text: str) -> list[Resource]:
    """The downloadable images and files ``text`` refers to, once per key, in order."""
    found: dict[str, Resource] = {}
    for match in _MARKER_RE.finditer(text or ""):
        key = match.group("image") or match.group("post_image") or match.group("file")
        if key not in found:
            is_file = bool(match.group("file"))
            found[key] = Resource(key, "file" if is_file else "image",
                                  html.unescape(match.group("name")) if is_file else "")
    return list(found.values())


def safe_name(name: str) -> str:
    """A sender-chosen file name as the tail of a cache file name: no directory part, no control or
    reserved characters, bounded length, extension kept."""
    base = Path(name.replace("\\", "/")).name
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*').strip(" .")
    stem, ext = os.path.splitext(base)
    if len(ext) > 16:
        stem, ext = base, ""
    return (stem or "file")[: _MAX_NAME_CHARS - len(ext)] + ext


def _lookup(res: Resource) -> tuple[Path, Optional[Path]]:
    """The cache directory for ``res`` and the file already cached under its key, if any."""
    directory = get_image_cache_dir() if res.is_image else get_document_cache_dir()
    for path in directory.glob(f"lark_{res.key}.*"):
        if path.is_file() and path.stat().st_size:
            os.utime(path)  # the cache sweep goes by age: a file still in use must not look old
            return directory, path
    return directory, None


def _keep(saved: Path, work: str, directory: Path, kind: str) -> Path:
    """Move what lark-cli saved in ``work`` into the cache ``directory``."""
    # Real paths on both sides: lark-cli reports /tmp as /private/tmp on macOS.
    if not saved.is_file() or saved.resolve().parent != Path(work).resolve():
        raise LarkCliError("api", "lark-cli reported no saved file in its working directory")
    validate_inbound_media_size(saved.stat().st_size, media_type=kind)
    dest = directory / saved.name
    shutil.move(str(saved), str(dest))
    return dest


def _media_type(path: Path) -> str:
    ext = path.suffix.lower()
    return (SUPPORTED_IMAGE_DOCUMENT_TYPES.get(ext) or SUPPORTED_DOCUMENT_TYPES.get(ext)
            or mimetypes.guess_type(path.name)[0] or "application/octet-stream")


async def _download(cli: LarkCli, message_id: str, res: Resource, directory: Path) -> Path:
    # lark-cli appends the extension it infers when the output name has none (images).
    output = f"lark_{res.key}" + (f".{safe_name(res.name)}" if res.kind == "file" else "")
    with tempfile.TemporaryDirectory(prefix="lark_user_dl_") as work:
        saved = await cli.download_resource(message_id, res.key, kind=res.kind, output=output, cwd=work)
        return await asyncio.to_thread(_keep, Path(saved), work, directory, res.kind)


async def _fetch(cli: LarkCli, message_id: str, res: Resource, gate: asyncio.Semaphore) -> Optional[Path]:
    directory, hit = await asyncio.to_thread(_lookup, res)
    if hit is not None:
        return hit
    async with gate:
        try:
            return await _download(cli, message_id, res, directory)
        except LarkCliError as exc:
            reason = exc.kind
        except (OSError, ValueError) as exc:  # disk trouble, or over gateway.max_inbound_media_bytes
            reason = type(exc).__name__
    logger.warning("lark_user: %s %s of %s not downloaded (%s); the agent sees its marker only",
                   res.kind, res.key, message_id, reason)
    return None


async def fetch_attachments(cli: LarkCli, messages: Iterable[tuple[str, str]]) -> list[Attachment]:
    """The cached files behind the image/file markers of ``(message_id, text)`` pairs, in message then
    marker order. A key met in several messages is fetched once, through the first message naming it."""
    wanted: dict[str, tuple[str, Resource]] = {}
    for message_id, text in messages:
        for res in find_resources(text):
            wanted.setdefault(res.key, (message_id, res))
    if not wanted:
        return []
    gate = asyncio.Semaphore(_PARALLEL_DOWNLOADS)
    paths = await asyncio.gather(*(_fetch(cli, mid, res, gate) for mid, res in wanted.values()))
    return [Attachment(str(path), _media_type(path), res.name)
            for (_, res), path in zip(wanted.values(), paths) if path is not None]
