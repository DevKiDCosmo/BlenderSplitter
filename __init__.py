bl_info = {
    "name": "Distributed Tile Renderer",
    "blender": (5, 1, 0),
    "category": "Render",
    "version": (0, 3, 0),
    "author": "BlenderSplitter",
    "description": "Robust distributed tile rendering over WebSocket",
}

import bpy

from . import ui
from .worker import manager


def _startup() -> None:
    mgr = manager()
    if not mgr.started:
        mgr.configure(
            host="0.0.0.0",
            server_port=8765,
            discovery_port=8766,
            overlap_percent=3.0,
            max_retries=3,
            auto_sync_project=True,
            show_render_window=True,
            server_render_tiles=True,
            output_dir="",
        )
    return None


def register():
    ui.register()
    bpy.app.timers.register(_startup, first_interval=0.5, persistent=True)


def unregister():
    manager().stop()
    ui.unregister()
