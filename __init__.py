bl_info = {
    "name": "Distributed Tile Renderer",
    "blender": (5, 1, 0),
    "category": "Render",
    "version": (0, 3, 0),
    "author": "DevKiD",
    "description": "Robust distributed tile rendering over WebSocket",
}

# Expose a single-source-of-truth version string read from the VERSION file when present.
__version__ = "dev"
try:
    ver_path = Path(__file__).resolve().parent / "VERSION"
    if ver_path.exists():
        __version__ = ver_path.read_text(encoding="utf-8").strip()
except Exception:
    __version__ = "dev"

import bpy
import json
from pathlib import Path

from . import ui
from .worker import manager


def _load_runtime_config() -> dict:
    root = Path(__file__).resolve().parent
    cfg_path = root / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _startup() -> None:
    mgr = manager()
    cfg = _load_runtime_config()
    network = cfg.get("network", {}) if isinstance(cfg, dict) else {}
    render = cfg.get("render", {}) if isinstance(cfg, dict) else {}
    scheduler = cfg.get("external_scheduler", {}) if isinstance(cfg, dict) else {}

    mode = str(cfg.get("mode", "user")) if isinstance(cfg, dict) else "user"
    user_mode = str(cfg.get("user_mode", "master_worker")) if isinstance(cfg, dict) else "master_worker"
    always = cfg.get("always", []) if isinstance(cfg, dict) else []
    if not isinstance(always, list):
        always = []

    mgr.configure_runtime_modes(mode, user_mode, always)
    mgr.configure_external_scheduler(
        enabled=bool(scheduler.get("enabled", False)),
        script=str(scheduler.get("script", "scheduler_app.py")),
        host=str(scheduler.get("host", "127.0.0.1")),
        port=int(scheduler.get("port", 9876)),
    )

    if not mgr.started:
        mgr.configure(
            host=str(network.get("host", "0.0.0.0")),
            server_port=int(network.get("server_port", 8765)),
            discovery_port=int(network.get("discovery_port", 8766)),
            overlap_percent=float(render.get("overlap_percent", 3.0)),
            max_retries=int(render.get("max_retries", 3)),
            auto_sync_project=bool(render.get("auto_sync_project", True)),
            show_render_window=bool(render.get("show_render_window", True)),
            server_render_tiles=bool(render.get("server_render_tiles", True)),
            tile_coefficient=int(render.get("tile_coefficient", 1)),
            output_dir=str(render.get("output_dir", "")),
        )
        if mgr.external_scheduler_enabled:
            mgr.start_external_scheduler(Path(__file__).resolve().parent / "config.json")
    return None


def register():
    ui.register()
    bpy.app.timers.register(_startup, first_interval=0.5, persistent=True)


def unregister():
    manager().stop()
    ui.unregister()
