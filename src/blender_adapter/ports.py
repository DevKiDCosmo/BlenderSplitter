"""Protocols that isolate bpy calls."""

from __future__ import annotations

from typing import Protocol


class BlenderOpsPort(Protocol):
    def render_tile(self, tile_payload: dict[str, object]) -> dict[str, object]: ...

    def open_scene(self, blend_path: str) -> None: ...

    def reset_to_blank(self) -> None: ...

    def collect_sync_files(self) -> list[str]: ...
