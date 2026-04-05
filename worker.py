"""Compatibility wrapper for legacy worker module."""

try:
    from .src.legacy.worker import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.worker import *  # type: ignore[F401,F403]
