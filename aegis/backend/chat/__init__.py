"""Aegis real-time chat module."""

from .routes import build_chat_router
from .service import ChatSessionManager

__all__ = ["ChatSessionManager", "build_chat_router"]
