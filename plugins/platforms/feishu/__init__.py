# Absolute import (not ``from .adapter``): the plugin loader executes this package under a synthetic
# ``hermes_plugins.platforms__feishu`` name, and the adapter's own absolute sibling imports would then
# load a second ``plugins.platforms.feishu.adapter`` whose lazily bound SDK names (``lark``,
# ``FeishuWSClient``, the card-callback models) stay None. One canonical name = one FeishuAdapter class.
from plugins.platforms.feishu.adapter import register

__all__ = ["register"]
