"""Concrete bpy adapter with safe import boundary."""

from __future__ import annotations

class BlenderNotAvailableError(RuntimeError):
    pass


class BpyAdapter:
    def __init__(self) -> None:
        self._bpy_available: bool = False
        try:
            __import__("bpy")
            self._bpy_available = True
        except ImportError:
            self._bpy_available = False

    def _guard(self) -> None:
        if not self._bpy_available:
            raise BlenderNotAvailableError("bpy is not available outside Blender runtime")

    def render_tile(self, tile_payload: dict[str, object]) -> dict[str, object]:
        self._guard()
        return {"ok": False, "reason": "TODO: implement render_tile using bpy", "tile": tile_payload}

    def open_scene(self, _blend_path: str) -> None:
        self._guard()
        raise NotImplementedError

    def reset_to_blank(self) -> None:
        self._guard()
        raise NotImplementedError

    def collect_sync_files(self) -> list[str]:
        self._guard()
        raise NotImplementedError
