"""Compatibility wrapper for legacy scheduler_app module."""

try:
    from .src.legacy.scheduler_app import *  # type: ignore[F401,F403]
except ImportError:
    from src.legacy.scheduler_app import *  # type: ignore[F401,F403]
