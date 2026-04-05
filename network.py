"""Compatibility wrapper for legacy network module."""

try:
    from .src.legacy.network import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.network import *  # type: ignore[F401,F403]
