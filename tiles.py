"""Compatibility wrapper for legacy tiles module."""

try:
    from .src.legacy.tiles import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.tiles import *  # type: ignore[F401,F403]
