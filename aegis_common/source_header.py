"""The Aegis ``<source>`` identity header: one compact JSON object a model reads to tell who is asking.

    <source>{"platform":"feishu","channel":"oc_…","uid":"ou_…","uname":"Ada"}</source>

Every surface that writes one goes through :func:`format_source_header` (gateway Slack/Feishu turns
in ``plugins.aegis_source``, the remote A2A envelope in ``plugins.aegis_a2a``, the Aegis console
chat); each caller only picks its fields.
"""

from __future__ import annotations

import json
from collections.abc import Mapping


def format_source_header(fields: Mapping[str, str]) -> str:
    """``<source>{fields}</source>``, keys in the given order.

    Values such as a display name are user-controlled: each is collapsed to one inert line, and the
    angle brackets inside the JSON strings are escaped so no value can close the tag early.
    """
    from gateway.session import neutralize_untrusted_inline_text

    data = {key: neutralize_untrusted_inline_text(value) for key, value in fields.items()}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return "<source>" + payload.replace("<", "\\u003c").replace(">", "\\u003e") + "</source>"
