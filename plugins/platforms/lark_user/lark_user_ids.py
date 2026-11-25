"""Sender open_id -> tenant ``user_id``, the id a turn names its speaker by.

lark-cli reports senders by the open_id its own app sees; that id differs per app, so the Feishu
bot and Aegis know the same colleague by another one. The tenant ``user_id`` is the same for every
app in the tenant, so the turn's speaker (``MessageEvent.user_id``: the ``<source>`` header's
``uid``) carries it. Routing keeps the open_id: session keys, allowlists and mentions are built on it.

One ``mget`` per poll resolves the senders not seen before; a sender with no tenant user_id
(another tenant) or a failed lookup leaves the open_id in place.
"""

from __future__ import annotations

import collections
import logging
from typing import Iterable

from .lark_user_cli import LarkCli, LarkCliError
from .lark_user_normalize import Inbound

logger = logging.getLogger(__name__)

_CACHE = 2000


class TenantIds:
    def __init__(self, cli: LarkCli):
        self._cli = cli
        self._ids: collections.OrderedDict[str, str] = collections.OrderedDict()  # open_id -> user_id or ""

    def _remember(self, open_id: str, user_id: str) -> None:
        self._ids[open_id] = user_id
        self._ids.move_to_end(open_id)
        while len(self._ids) > _CACHE:
            self._ids.popitem(last=False)

    async def learn(self, inbound: Iterable[Inbound]) -> None:
        """Look up the tenant user_id of every sender in ``inbound`` not resolved yet."""
        unknown = {m.sender_id: m.message_id for m in inbound if m.sender_id and m.sender_id not in self._ids}
        if not unknown:
            return
        try:
            found = await self._cli.sender_user_ids(list(unknown.values()))
        except LarkCliError as exc:
            logger.warning("lark_user: sender user_id lookup failed (%s); turns name these senders by open_id",
                           exc.kind)
            return
        for open_id, message_id in unknown.items():
            self._remember(open_id, found.get(message_id, ""))

    def of(self, open_id: str) -> str:
        """The tenant user_id of ``open_id``, else ``open_id`` itself."""
        return self._ids.get(open_id) or open_id
