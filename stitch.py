"""Compatibility wrapper for legacy stitch module."""

try:
    from .src.legacy.stitch import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.stitch import *  # type: ignore[F401,F403]
