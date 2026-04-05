"""Compatibility wrapper for legacy robust_connection module."""

try:
    from .src.legacy.robust_connection import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.robust_connection import *  # type: ignore[F401,F403]
