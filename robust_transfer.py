"""Compatibility wrapper for legacy robust_transfer module."""

try:
    from .src.legacy.robust_transfer import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.robust_transfer import *  # type: ignore[F401,F403]
