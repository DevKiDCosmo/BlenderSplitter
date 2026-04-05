"""Compatibility wrapper for legacy robust_protocol module."""

try:
    from .src.legacy.robust_protocol import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.robust_protocol import *  # type: ignore[F401,F403]
