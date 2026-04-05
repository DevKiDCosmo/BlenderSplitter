"""Compatibility wrapper for legacy ui module."""

try:
    from .src.legacy.ui import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.ui import *  # type: ignore[F401,F403]
