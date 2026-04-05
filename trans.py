"""Compatibility wrapper for legacy trans module."""

try:
    from .src.legacy.trans import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.trans import *  # type: ignore[F401,F403]
