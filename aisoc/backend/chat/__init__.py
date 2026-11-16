"""AISOC real-time chat module.

Forked from ``aegis/backend/chat`` (2026-08) and de-user-ized for AISOC's
single-token deployment. Keep the WebSocket event protocol in sync with the
Aegis implementation until a shared ``chat_core`` package exists.
"""

from .routes import build_agent_chat_router
from .service import ChatSessionManager

__all__ = ["ChatSessionManager", "build_agent_chat_router"]
