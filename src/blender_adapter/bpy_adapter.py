"""Concrete bpy adapter with safe import boundary."""

from __future__ import annotations

import os
import tempfile


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
        """Render a single tile defined by border coordinates via bpy.

        ``tile_payload`` must contain ``min_x``, ``max_x``, ``min_y``,
        ``max_y`` as normalised [0,1] floats and a ``tile_id`` string.
        Returns a result dict with ``ok``, ``tile_id`` and (on success) a
        ``png_path`` pointing at the rendered image.
        """
        self._guard()
        import bpy  # type: ignore[import]

        tile_id = str(tile_payload.get("tile_id", "unknown"))
        try:
            scene = bpy.context.scene
            render = scene.render

            min_x = float(tile_payload.get("min_x", 0))
            max_x = float(tile_payload.get("max_x", 1))
            min_y = float(tile_payload.get("min_y", 0))
            max_y = float(tile_payload.get("max_y", 1))

            res_x = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
            res_y = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))

            render.use_border = True
            render.use_crop_to_border = True
            render.border_min_x = min_x / float(res_x)
            render.border_max_x = max_x / float(res_x)
            render.border_min_y = min_y / float(res_y)
            render.border_max_y = max_y / float(res_y)

            out_path = os.path.join(tempfile.gettempdir(), f"bsplitter_tile_{tile_id}.png")
            prev_filepath = render.filepath
            prev_format = render.image_settings.file_format
            try:
                render.filepath = out_path
                render.image_settings.file_format = "PNG"
                bpy.ops.render.render(write_still=True)
            finally:
                render.filepath = prev_filepath
                render.image_settings.file_format = prev_format

            return {"ok": True, "tile_id": tile_id, "png_path": out_path}
        except Exception as exc:
            return {"ok": False, "tile_id": tile_id, "reason": str(exc)}

    def open_scene(self, blend_path: str) -> None:
        """Open a .blend file, replacing the current scene."""
        self._guard()
        import bpy  # type: ignore[import]

        bpy.ops.wm.open_mainfile(filepath=blend_path)

    def reset_to_blank(self) -> None:
        """Reset Blender to a blank scene (factory settings, empty startup)."""
        self._guard()
        import bpy  # type: ignore[import]

        bpy.ops.wm.read_factory_settings(use_empty=True)

    def collect_sync_files(self) -> list[str]:
        """Collect all project files referenced by the current .blend.

        Returns absolute paths to the saved .blend and all externally
        referenced assets (images, sounds, libraries, movie clips).
        """
        self._guard()
        import bpy  # type: ignore[import]

        paths: list[str] = []

        blend_path = bpy.data.filepath
        if blend_path:
            paths.append(os.path.abspath(blend_path))

        for img in bpy.data.images:
            abspath = bpy.path.abspath(img.filepath)
            if abspath and os.path.isfile(abspath):
                paths.append(os.path.abspath(abspath))

        for sound in bpy.data.sounds:
            abspath = bpy.path.abspath(sound.filepath)
            if abspath and os.path.isfile(abspath):
                paths.append(os.path.abspath(abspath))

        for lib in bpy.data.libraries:
            abspath = bpy.path.abspath(lib.filepath)
            if abspath and os.path.isfile(abspath):
                paths.append(os.path.abspath(abspath))

        for clip in bpy.data.movieclips:
            abspath = bpy.path.abspath(clip.filepath)
            if abspath and os.path.isfile(abspath):
                paths.append(os.path.abspath(abspath))

        return list(dict.fromkeys(paths))
