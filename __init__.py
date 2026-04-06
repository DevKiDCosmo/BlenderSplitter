bl_info = {
    "name": "Distributed Tile Renderer",
    "blender": (5, 1, 0),
    "category": "Render",
    "version": (0, 3, 0),
    "author": "DevKiD",
    "description": "Robust distributed tile rendering over WebSocket",
}

import json
from pathlib import Path

# Expose a single-source-of-truth version string read from the VERSION file when present.
__version__ = "dev"
try:
    ver_path = Path(__file__).resolve().parent / "VERSION"
    if ver_path.exists():
        __version__ = ver_path.read_text(encoding="utf-8").strip()
except Exception:
    __version__ = "dev"

try:
    import bpy
    _BPY_AVAILABLE = True
except ImportError:
    bpy = None  # type: ignore[assignment]
    _BPY_AVAILABLE = False

if _BPY_AVAILABLE:
    from .src.legacy import ui
    from .src.runtime.facade import RuntimeConfig, SplitterRuntimeFacade

    _addon_facade: "SplitterRuntimeFacade | None" = None

    def _load_runtime_config() -> dict:
        root = Path(__file__).resolve().parent
        cfg_path = root / "config.json"
        if not cfg_path.exists():
            return {}
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _build_runtime_config(cfg: dict) -> "RuntimeConfig":
        network = cfg.get("network", {}) if isinstance(cfg, dict) else {}
        render = cfg.get("render", {}) if isinstance(cfg, dict) else {}
        scheduler = cfg.get("external_scheduler", {}) if isinstance(cfg, dict) else {}
        mode = str(cfg.get("mode", "user")) if isinstance(cfg, dict) else "user"

        return RuntimeConfig(
            host=str(network.get("host", "0.0.0.0")),
            server_port=int(network.get("server_port", 8765)),
            discovery_port=int(network.get("discovery_port", 8766)),
            startup_mode=mode,
            overlap_percent=float(render.get("overlap_percent", 3.0)),
            tile_coefficient=int(render.get("tile_coefficient", 1)),
            max_retries=int(render.get("max_retries", 3)),
            auto_sync_project=bool(render.get("auto_sync_project", True)),
            show_render_window=bool(render.get("show_render_window", True)),
            server_render_tiles=bool(render.get("server_render_tiles", True)),
            output_dir=str(render.get("output_dir", "")),
            external_scheduler_enabled=bool(scheduler.get("enabled", False)),
            external_scheduler_script=str(scheduler.get("script", "scheduler_app.py")),
            external_scheduler_host=str(scheduler.get("host", "127.0.0.1")),
            external_scheduler_port=int(scheduler.get("port", 9876)),
        )

    def _startup() -> None:
        global _addon_facade
        cfg_dict = _load_runtime_config()
        runtime_config = _build_runtime_config(cfg_dict)

        if _addon_facade is None:
            _addon_facade = SplitterRuntimeFacade(runtime_config)
        else:
            _addon_facade.update_config(runtime_config)

        # Also configure the legacy manager for the transition period so that
        # components that still access it directly stay in sync.
        mgr = _addon_facade._get_legacy_manager()  # type: ignore[attr-defined]
        if mgr is not None and not mgr.started:
            cfg = cfg_dict if isinstance(cfg_dict, dict) else {}
            scheduler_cfg = cfg.get("external_scheduler", {}) if isinstance(cfg, dict) else {}
            mode = str(cfg.get("mode", "user"))
            user_mode = str(cfg.get("user_mode", "master_worker"))
            always = cfg.get("always", [])
            if not isinstance(always, list):
                always = []
            mgr.configure_runtime_modes(mode, user_mode, always)
            mgr.configure_external_scheduler(
                enabled=bool(scheduler_cfg.get("enabled", False)),
                script=str(scheduler_cfg.get("script", "scheduler_app.py")),
                host=str(scheduler_cfg.get("host", "127.0.0.1")),
                port=int(scheduler_cfg.get("port", 9876)),
            )
            if mgr.external_scheduler_enabled:
                mgr.start_external_scheduler(Path(__file__).resolve().parent / "config.json")
        return None

    def register():
        ui.register()
        bpy.app.timers.register(_startup, first_interval=0.5, persistent=True)

    def unregister():
        global _addon_facade
        if _addon_facade is not None:
            _addon_facade.shutdown()
            _addon_facade = None
        ui.unregister()
