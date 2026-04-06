import math
import os
import tempfile
import time
import uuid

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..runtime.facade import RuntimeConfig, SplitterRuntimeFacade
from ..ui.controller import UiController
from .tiles import generate_tiles, grid_for_tile_count, tile_target_for_workers, overlap_pixels

ADDON_VERSION = "0.3.0"
ADDON_DEVELOPER = "DevKiD"

_preview_handler = None
_camera_border_handler = None
_ui_refresh_registered = False
_runtime_facade = SplitterRuntimeFacade()
_ui_controller = UiController(_runtime_facade)


def _get_mgr():
    """Return the legacy manager for read-only display use only."""
    return _ui_controller.get_legacy_manager_for_display()


def _runtime_config_from_settings(cfg):
    return RuntimeConfig(
        host=cfg.host,
        server_port=cfg.server_port,
        discovery_port=cfg.discovery_port,
        startup_mode=_ui_controller.get_effective_mode(),
        output_dir=cfg.output_dir,
        overlap_percent=cfg.overlap_percent,
        tile_coefficient=cfg.tile_coefficient,
        max_retries=cfg.max_retries,
        auto_sync_project=cfg.auto_sync_project,
        show_render_window=cfg.show_render_window,
        server_render_tiles=cfg.server_render_tiles,
    )


def _apply_runtime_controller_config(context):
    cfg = context.scene.blendersplitter_settings
    _ui_controller.apply_config(_runtime_config_from_settings(cfg))


def _tag_redraw_all(context=None):
    ctx = context or bpy.context
    if ctx is None:
        return
    wm = ctx.window_manager
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            area.tag_redraw()


def _sync_runtime_settings(context=None):
    ctx = context or bpy.context
    if ctx is None or ctx.scene is None or not hasattr(ctx.scene, "blendersplitter_settings"):
        return

    _ui_controller.apply_config(_runtime_config_from_settings(ctx.scene.blendersplitter_settings))
    _tag_redraw_all(ctx)


def _settings_updated(self, context):
    _sync_runtime_settings(context)


def _ui_refresh_tick():
    if not _ui_refresh_registered:
        return None
    try:
        model = _ui_controller.panel_model()
        mgr = _get_mgr()
        active = model.workers_online > 0 or (mgr is not None and (mgr.started or mgr.sync_active or mgr.pending_jobs))
        if active:
            wm = bpy.context.window_manager if bpy.context else None
            if wm:
                for window in wm.windows:
                    screen = window.screen
                    if not screen:
                        continue
                    for area in screen.areas:
                        area.tag_redraw()
    except Exception:
        pass
    return 0.5


def _color_for_target(target):
    if target == "MASTER":
        return (0.1, 0.8, 0.1, 0.65)
    h = abs(hash(str(target)))
    r = ((h >> 0) & 255) / 255.0
    g = ((h >> 8) & 255) / 255.0
    b = ((h >> 16) & 255) / 255.0
    return (0.2 + 0.6 * r, 0.2 + 0.6 * g, 0.2 + 0.6 * b, 0.6)


def _build_preview_plan(cfg, mgr):
    if mgr is not None and mgr.current_render_config and mgr.render_plan:
        return mgr.render_plan

    scene = bpy.context.scene
    render = scene.render
    res_x = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
    res_y = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))

    if mgr is not None and mgr.role == "server" and mgr.started and mgr.connected_workers:
        total_nodes = len(mgr.connected_workers) + (1 if cfg.server_render_tiles else 0)
    else:
        total_nodes = cfg.worker_count + (1 if cfg.server_render_tiles else 0)

    tile_count = tile_target_for_workers(max(1, total_nodes), cfg.tile_coefficient)
    grid_x, grid_y = grid_for_tile_count(tile_count, res_x, res_y)
    overlap = overlap_pixels(res_x, res_y, cfg.overlap_percent)
    tiles = generate_tiles(res_x, res_y, grid_x, grid_y, overlap=overlap)

    targets = []
    if cfg.server_render_tiles:
        targets.append("MASTER")
    for i in range(cfg.worker_count):
        targets.append(f"W{i + 1}")
    if not targets:
        targets = ["MASTER"]

    plan = []
    for idx, tile in enumerate(tiles):
        plan.append(
            {
                "tile_id": tile["id"],
                "target": targets[idx % len(targets)],
                "min_x": tile["min_x"],
                "max_x": tile["max_x"],
                "min_y": tile["min_y"],
                "max_y": tile["max_y"],
                "core_min_x": tile["core_min_x"],
                "core_max_x": tile["core_max_x"],
                "core_min_y": tile["core_min_y"],
                "core_max_y": tile["core_max_y"],
            }
        )
    return plan


def _draw_preview_callback():
    try:
        context = bpy.context
        region = context.region
        if region is None:
            return
        mgr = _get_mgr()
        cfg = context.scene.blendersplitter_settings
        plan = _build_preview_plan(cfg, mgr)

        box_w = 240
        box_h = 240
        margin = 16
        x0 = region.width - box_w - margin
        y0 = region.height - box_h - margin
        x1 = x0 + box_w
        y1 = y0 + box_h

        shader = gpu.shader.from_builtin("2D_UNIFORM_COLOR")
        bg = [
            (x0, y0),
            (x1, y0),
            (x1, y1),
            (x0, y0),
            (x1, y1),
            (x0, y1),
        ]
        batch = batch_for_shader(shader, "TRIS", {"pos": bg})
        shader.bind()
        shader.uniform_float("color", (0.05, 0.05, 0.05, 0.85))
        batch.draw(shader)

        outline = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": outline})
        shader.uniform_float("color", (0.9, 0.9, 0.9, 0.8))
        batch.draw(shader)

        if not plan:
            blf.position(0, x0 + 8, y1 - 20, 0)
            blf.size(0, 13.0)
            blf.draw(0, "No plan")
            return

        res_x = max((int(p.get("max_x", 1)) for p in plan), default=1)
        res_y = max((int(p.get("max_y", 1)) for p in plan), default=1)

        for item in plan:
            min_x = float(item.get("min_x", 0))
            max_x = float(item.get("max_x", 0))
            min_y = float(item.get("min_y", 0))
            max_y = float(item.get("max_y", 0))

            nx0 = min_x / max(1.0, float(res_x))
            nx1 = max_x / max(1.0, float(res_x))
            ny0 = min_y / max(1.0, float(res_y))
            ny1 = max_y / max(1.0, float(res_y))

            px0 = x0 + nx0 * box_w
            px1 = x0 + nx1 * box_w
            py0 = y0 + (1.0 - ny1) * box_h
            py1 = y0 + (1.0 - ny0) * box_h

            verts = [
                (px0, py0),
                (px1, py0),
                (px1, py1),
                (px0, py0),
                (px1, py1),
                (px0, py1),
            ]
            batch = batch_for_shader(shader, "TRIS", {"pos": verts})
            shader.uniform_float("color", _color_for_target(item.get("target")))
            batch.draw(shader)

            blf.position(0, px0 + 3, py1 - 14, 0)
            blf.size(0, 10.0)
            blf.draw(0, str(item.get("tile_id", "?")))
    except Exception:
        return


def _draw_camera_border_callback():
    try:
        context = bpy.context
        if context is None or context.region is None or context.region_data is None:
            return
        if getattr(context.region_data, "view_perspective", "") != "CAMERA":
            return

        mgr = _get_mgr()
        cfg = context.scene.blendersplitter_settings
        plan = _build_preview_plan(cfg, mgr)
        if not plan:
            return

        region = context.region
        render = context.scene.render
        aspect = (
            float(render.resolution_x) * float(getattr(render, "pixel_aspect_x", 1.0))
        ) / max(1e-6, float(render.resolution_y) * float(getattr(render, "pixel_aspect_y", 1.0)))
        region_aspect = float(region.width) / max(1.0, float(region.height))

        if region_aspect > aspect:
            frame_h = float(region.height) * 0.86
            frame_w = frame_h * aspect
        else:
            frame_w = float(region.width) * 0.86
            frame_h = frame_w / max(1e-6, aspect)

        x0 = (float(region.width) - frame_w) * 0.5
        y0 = (float(region.height) - frame_h) * 0.5
        x1 = x0 + frame_w
        y1 = y0 + frame_h

        shader = gpu.shader.from_builtin("2D_UNIFORM_COLOR")
        outline = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": outline})
        shader.bind()
        shader.uniform_float("color", (0.95, 0.85, 0.15, 0.95))
        batch.draw(shader)

        res_x = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
        res_y = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))

        for item in plan:
            min_x = float(item.get("min_x", 0)) / float(max(1, res_x))
            max_x = float(item.get("max_x", 0)) / float(max(1, res_x))
            min_y = float(item.get("min_y", 0)) / float(max(1, res_y))
            max_y = float(item.get("max_y", 0)) / float(max(1, res_y))

            px0 = x0 + min_x * frame_w
            px1 = x0 + max_x * frame_w
            py0 = y0 + (1.0 - max_y) * frame_h
            py1 = y0 + (1.0 - min_y) * frame_h

            verts = [
                (px0, py0),
                (px1, py0),
                (px1, py1),
                (px0, py0),
                (px1, py1),
                (px0, py1),
            ]
            batch = batch_for_shader(shader, "TRIS", {"pos": verts})
            shader.uniform_float("color", _color_for_target(item.get("target")))
            batch.draw(shader)
    except Exception:
        return


def _draw_cluster_monitor(layout, mgr):
    box = layout.box()
    box.label(text="Cluster Monitor")
    if mgr is None:
        box.label(text="Runtime unavailable")
        return
    box.label(text=f"Role: {mgr.role}")
    box.label(text=f"Started: {'yes' if mgr.started else 'no'}")
    box.label(text=f"Endpoint: {mgr.server_host}:{mgr.server_port}")
    box.label(text=f"Status: {mgr.status}")
    box.label(text=f"Workers: {len(mgr.connected_workers)}")
    box.label(text=f"Jobs pending/done: {len(mgr.pending_jobs)}/{len(mgr.completed_jobs)}")
    box.label(text=f"Integrity: {mgr.last_integrity}")
    box.label(text=f"Render Time: {mgr.last_duration_seconds:.2f}s")

    stats = mgr.transfer_stats or {}
    box.label(
        text=(
            f"Transfer inline/chunked: {int(stats.get('tiles_inline', 0))}/"
            f"{int(stats.get('tiles_chunked', 0))}"
        )
    )

    if mgr.current_output_root:
        box.label(text=f"Run: {mgr.current_output_root}")
    if mgr.current_master_dir:
        box.label(text=f"Master: {mgr.current_master_dir}")
    if mgr.current_raw_splits_dir:
        box.label(text=f"Raw-Splits: {mgr.current_raw_splits_dir}")

    pkg = mgr.sync_package_info or {}
    if pkg:
        box.label(
            text=(
                f"Sync Package: {int(pkg.get('file_count', 0))} Dateien | "
                f"Quelle {int(pkg.get('source_total_size', 0)) // (1024*1024)}MB | "
                f"Archiv {int(pkg.get('archive_total_size', 0)) // (1024*1024)}MB"
            )
        )
        box.label(text=f"Sync Chunks: {int(pkg.get('chunk_count', 0))}")

    if mgr.last_error:
        err = layout.box()
        err.label(text=f"Error: {mgr.last_error}")

    if mgr.connected_workers:
        workers_box = layout.box()
        workers_box.label(text="Worker Connections")
        now = time.time()
        for worker_id, info in sorted(mgr.connected_workers.items()):
            last_seen = float(info.get("last_seen", 0.0))
            age = max(0.0, now - last_seen) if last_seen else -1.0
            age_text = f"{age:.1f}s" if age >= 0.0 else "n/a"
            app = info.get("app", "unknown")
            sync_state = (mgr.worker_sync_state or {}).get(worker_id, {})
            phase = sync_state.get("phase", "idle")
            cur = int(sync_state.get("current_bytes", 0))
            total = int(sync_state.get("total_bytes", 0))
            pct = (float(cur) / float(total) * 100.0) if total else 0.0
            workers_box.label(text=f"{worker_id[:8]} | seen {age_text} | {app}")
            workers_box.label(text=f"Sync: {phase} | {pct:.1f}%")


class BLENDERSPLITTER_OT_start_network(bpy.types.Operator):
    bl_idname = "blendersplitter.start_network"
    bl_label = "Start Cluster"

    def execute(self, context):
        _apply_runtime_controller_config(context)
        _ui_controller.start_runtime()
        self.report({"INFO"}, "Cluster gestartet")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_stop_network(bpy.types.Operator):
    bl_idname = "blendersplitter.stop_network"
    bl_label = "Stop Cluster"

    def execute(self, context):
        _ui_controller.stop_runtime()
        self.report({"INFO"}, "Cluster gestoppt")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_start_server(bpy.types.Operator):
    bl_idname = "blendersplitter.start_server"
    bl_label = "Force Server"
    bl_description = (
        "Take over server role from the current master. "
        "Only available when this node is connected as a worker."
    )

    @classmethod
    def poll(cls, context):
        """Enable only when this node is already connected as a worker."""
        mgr = _get_mgr()
        if mgr is None:
            return False
        return bool(mgr.started and mgr.role == "worker")

    def execute(self, context):
        _apply_runtime_controller_config(context)
        ok = _ui_controller.force_start_server()
        if ok:
            self.report({"INFO"}, "Server gestartet")
            return {"FINISHED"}
        self.report({"ERROR"}, _ui_controller.last_error() or "Serverstart fehlgeschlagen")
        return {"CANCELLED"}


class BLENDERSPLITTER_OT_cluster_monitor_popup(bpy.types.Operator):
    bl_idname = "blendersplitter.cluster_monitor_popup"
    bl_label = "Cluster Monitor"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        _draw_cluster_monitor(self.layout, _get_mgr())


class BLENDERSPLITTER_OT_install_requirements(bpy.types.Operator):
    bl_idname = "blendersplitter.install_requirements"
    bl_label = "Install Requirements"

    def execute(self, context):
        ok = _ui_controller.auto_install_requirements()
        if not ok:
            self.report({"ERROR"}, _ui_controller.last_error())
            return {"CANCELLED"}
        self.report({"INFO"}, "Requirements installiert")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_dry_run_integrity(bpy.types.Operator):
    bl_idname = "blendersplitter.dry_run_integrity"
    bl_label = "Dry Run Integrity"

    def execute(self, context):
        ok = _ui_controller.run_integrity_check(timeout_s=5.0)
        if not ok:
            self.report({"ERROR"}, _ui_controller.last_error() or "Integrity check failed")
            return {"CANCELLED"}
        self.report({"INFO"}, "Integrity OK")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_sync_project_files(bpy.types.Operator):
    bl_idname = "blendersplitter.sync_project_files"
    bl_label = "Sync Project Files"

    def execute(self, context):
        _apply_runtime_controller_config(context)
        prev_error = _ui_controller.last_error()
        _ui_controller.sync_project()
        new_error = _ui_controller.last_error()
        if new_error and new_error != prev_error:
            self.report({"ERROR"}, new_error)
            return {"CANCELLED"}
        self.report({"INFO"}, _ui_controller.panel_model().headline)
        return {"FINISHED"}


class BLENDERSPLITTER_OT_distributed_render(bpy.types.Operator):
    bl_idname = "blendersplitter.distributed_render"
    bl_label = "Distributed Render"

    def execute(self, context):
        _apply_runtime_controller_config(context)
        prev_error = _ui_controller.last_error()
        _ui_controller.start_render()
        new_error = _ui_controller.last_error()
        if new_error and new_error != prev_error:
            self.report({"ERROR"}, new_error or "Render konnte nicht gestartet werden")
            return {"CANCELLED"}
        self.report({"INFO"}, "Render gestartet")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_abort_render(bpy.types.Operator):
    bl_idname = "blendersplitter.abort_render"
    bl_label = "Abort Render"

    def execute(self, context):
        _apply_runtime_controller_config(context)
        prev_error = _ui_controller.last_error()
        _ui_controller.cancel_render()
        new_error = _ui_controller.last_error()
        if new_error and new_error != prev_error:
            self.report({"ERROR"}, new_error)
            return {"CANCELLED"}
        self.report({"INFO"}, "Render abgebrochen")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_kick_all(bpy.types.Operator):
    bl_idname = "blendersplitter.kick_all"
    bl_label = "Kick All Workers"

    def execute(self, context):
        _ui_controller.kick_all_workers()
        self.report({"INFO"}, "Alle Worker getrennt")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_clean_worker_blends(bpy.types.Operator):
    bl_idname = "blendersplitter.clean_worker_blends"
    bl_label = "Clean Worker .blend"

    def execute(self, context):
        _apply_runtime_controller_config(context)
        prev_error = _ui_controller.last_error()
        _ui_controller.clean_workers()
        new_error = _ui_controller.last_error()
        if new_error and new_error != prev_error:
            self.report({"ERROR"}, new_error or "Clean fehlgeschlagen")
            return {"CANCELLED"}
        self.report({"INFO"}, "Clean-Befehl an Worker gesendet")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_toggle_preview_overlay(bpy.types.Operator):
    bl_idname = "blendersplitter.toggle_preview_overlay"
    bl_label = "Toggle Preview Overlay"

    def execute(self, context):
        global _preview_handler
        if _preview_handler is None:
            _preview_handler = bpy.types.SpaceView3D.draw_handler_add(_draw_preview_callback, (), "WINDOW", "POST_PIXEL")
            self.report({"INFO"}, "Overlay aktiv")
        else:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(_preview_handler, "WINDOW")
            except Exception:
                pass
            _preview_handler = None
            self.report({"INFO"}, "Overlay inaktiv")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_render_partition_image(bpy.types.Operator):
    bl_idname = "blendersplitter.render_partition_image"
    bl_label = "Render Partition Image"

    def execute(self, context):
        cfg = context.scene.blendersplitter_settings
        mgr = _get_mgr()
        plan = _build_preview_plan(cfg, mgr)
        if not plan:
            self.report({"ERROR"}, "Kein Plan verfügbar")
            return {"CANCELLED"}

        res_x = max((int(p.get("max_x", 1)) for p in plan), default=1)
        res_y = max((int(p.get("max_y", 1)) for p in plan), default=1)
        max_dim = 1024
        scale = min(1.0, float(max_dim) / max(1, max(res_x, res_y)))
        w = max(1, int(res_x * scale))
        h = max(1, int(res_y * scale))

        img_name = f"BlenderSplitter_Partition_{uuid.uuid4().hex[:8]}"
        img = bpy.data.images.new(img_name, width=w, height=h, alpha=True, float_buffer=False)
        pixels = [0.0] * (w * h * 4)

        def draw_rect(x0, y0, x1, y1, color):
            ix0 = max(0, min(w - 1, int(math.floor(x0))))
            ix1 = max(0, min(w, int(math.ceil(x1))))
            iy0 = max(0, min(h - 1, int(math.floor(y0))))
            iy1 = max(0, min(h, int(math.ceil(y1))))
            for yy in range(iy0, iy1):
                for xx in range(ix0, ix1):
                    i = (yy * w + xx) * 4
                    pixels[i] = color[0]
                    pixels[i + 1] = color[1]
                    pixels[i + 2] = color[2]
                    pixels[i + 3] = color[3]

        for item in plan:
            nx0 = float(item.get("min_x", 0)) / float(max(1, res_x))
            nx1 = float(item.get("max_x", 0)) / float(max(1, res_x))
            ny0 = float(item.get("min_y", 0)) / float(max(1, res_y))
            ny1 = float(item.get("max_y", 0)) / float(max(1, res_y))
            x0 = nx0 * w
            x1 = nx1 * w
            y0 = (1.0 - ny1) * h
            y1 = (1.0 - ny0) * h
            draw_rect(x0, y0, x1, y1, _color_for_target(item.get("target")))

        img.pixels = pixels
        tmp_path = os.path.join(tempfile.gettempdir(), f"{img.name}.png")
        img.filepath_raw = tmp_path
        img.file_format = "PNG"
        img.save()

        loaded = bpy.data.images.load(tmp_path, check_existing=True)
        wm = bpy.context.window_manager
        wm["bl_splitter_partition_image"] = loaded.name
        wm["bl_splitter_partition_tmp_path"] = tmp_path

        before = len(wm.windows)
        bpy.ops.wm.window_new()
        new_window = wm.windows[-1] if len(wm.windows) > before else bpy.context.window
        if new_window and new_window.screen and new_window.screen.areas:
            area = new_window.screen.areas[0]
            area.type = "IMAGE_EDITOR"
            area.spaces.active.image = loaded

        self.report({"INFO"}, f"Partition image erstellt: {loaded.name}")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_close_partition_image(bpy.types.Operator):
    bl_idname = "blendersplitter.close_partition_image"
    bl_label = "Close Partition Image"

    def execute(self, context):
        wm = bpy.context.window_manager
        img_name = wm.get("bl_splitter_partition_image")
        tmp_path = wm.get("bl_splitter_partition_tmp_path")

        removed = False
        if img_name and img_name in bpy.data.images:
            try:
                bpy.data.images.remove(bpy.data.images[img_name])
                removed = True
            except Exception:
                pass

        if tmp_path:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

        for k in ("bl_splitter_partition_image", "bl_splitter_partition_tmp_path"):
            if k in wm:
                del wm[k]

        if removed:
            self.report({"INFO"}, "Partition image entfernt")
            return {"FINISHED"}
        self.report({"WARNING"}, "Kein Partition image gefunden")
        return {"CANCELLED"}


class BLENDERSPLITTER_PG_settings_v2(bpy.types.PropertyGroup):
    host: bpy.props.StringProperty(name="Host", default="0.0.0.0", update=_settings_updated)
    server_port: bpy.props.IntProperty(name="Server Port", default=8765, min=1024, max=65535, update=_settings_updated)
    discovery_port: bpy.props.IntProperty(name="Discovery Port", default=8766, min=1024, max=65535, update=_settings_updated)
    overlap_percent: bpy.props.FloatProperty(name="Overlap %", default=3.0, min=2.0, max=8.0, update=_settings_updated)
    worker_count: bpy.props.IntProperty(name="Worker Count", default=4, min=1, max=256, update=_settings_updated)
    tile_coefficient: bpy.props.IntProperty(name="Tile Koeffizient", default=1, min=1, max=16, update=_settings_updated)
    max_retries: bpy.props.IntProperty(name="Max Retries", default=3, min=1, max=20, update=_settings_updated)
    auto_sync_project: bpy.props.BoolProperty(name="Auto Sync Project", default=False, update=_settings_updated)
    show_render_window: bpy.props.BoolProperty(name="Show Render Window", default=True, update=_settings_updated)
    server_render_tiles: bpy.props.BoolProperty(name="Server Render Tiles", default=True, update=_settings_updated)
    output_dir: bpy.props.StringProperty(name="Output Folder", subtype="DIR_PATH", default="", update=_settings_updated)


class BLENDERSPLITTER_OT_reset_runtime(bpy.types.Operator):
    bl_idname = "blendersplitter.reset_runtime"
    bl_label = "Update Information"
    bl_description = "Refresh runtime state and re-sync configuration from scene settings"

    def execute(self, context):
        if not _ui_controller.reset_runtime(hard=False):
            self.report({"ERROR"}, _ui_controller.last_error() or "Update failed")
            return {"CANCELLED"}
        _sync_runtime_settings(context)
        self.report({"INFO"}, "Information updated")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_hard_reset_runtime(bpy.types.Operator):
    bl_idname = "blendersplitter.hard_reset_runtime"
    bl_label = "Reset"
    bl_description = "Full reset: clear all state and restore default settings"

    def execute(self, context):
        if not _ui_controller.reset_runtime(hard=True):
            self.report({"ERROR"}, _ui_controller.last_error() or "Reset failed")
            return {"CANCELLED"}
        _sync_runtime_settings(context)
        self.report({"INFO"}, "Reset completed")
        return {"FINISHED"}


class BLENDERSPLITTER_PT_panel(bpy.types.Panel):
    bl_label = "Blender Splitter"
    bl_idname = "BLENDERSPLITTER_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Render"

    def draw(self, context):
        layout = self.layout
        cfg = context.scene.blendersplitter_settings
        mgr = _get_mgr()
        model = _ui_controller.panel_model()
        is_worker = model.role == "worker"

        header = layout.box()
        header.label(text="Blender Splitter")
        header.label(text=f"Version: {ADDON_VERSION}")
        header.label(text=f"Developer: {ADDON_DEVELOPER}")
        header.label(text=f"Mode (config): {_ui_controller.get_effective_mode()}")

        layout.label(text="Cluster Configuration")
        layout.prop(cfg, "host")
        layout.prop(cfg, "server_port")
        layout.prop(cfg, "discovery_port")
        layout.prop(cfg, "output_dir")
        layout.prop(cfg, "overlap_percent")
        layout.prop(cfg, "tile_coefficient")
        layout.prop(cfg, "max_retries")
        layout.prop(cfg, "auto_sync_project")
        layout.prop(cfg, "server_render_tiles")
        layout.prop(cfg, "show_render_window")

        summary = layout.box()
        plan = _build_preview_plan(cfg, mgr)
        summary.label(text="Tile Plan")
        summary.label(text=f"Planned Tiles: {len(plan)}")
        if mgr is not None and getattr(mgr, "expected_jobs", 0) > 0:
            summary.label(text=f"Rendered: {len(mgr.completed_jobs)}/{mgr.expected_jobs}")
            summary.label(text=f"Pending/In-Flight: {len(mgr.job_queue)}/{len(mgr.pending_jobs)}")

        has_workers = mgr is not None and bool(mgr.connected_workers)
        is_server = model.role == "server"

        row = layout.row(align=True)
        row.operator("blendersplitter.start_network", icon="PLAY")
        row.operator("blendersplitter.stop_network", icon="PAUSE")

        # Force Server: poll() disables it unless role == "worker"
        layout.operator("blendersplitter.start_server", icon="NETWORK_DRIVE")
        layout.operator("blendersplitter.cluster_monitor_popup", icon="WINDOW")

        layout.separator()
        layout.operator("blendersplitter.install_requirements", icon="CONSOLE")

        # Sync / Clean — visually consistent with start/stop (align=True).
        row = layout.row(align=True)
        row.enabled = not is_worker and has_workers
        row.operator("blendersplitter.sync_project_files", icon="FILE_REFRESH")
        row.operator("blendersplitter.clean_worker_blends", icon="TRASH")
        if not is_worker and not has_workers and model.started:
            layout.label(text="No workers connected yet", icon="INFO")

        # Update Information / Reset — available for all roles (workers can refresh
        # their own state too; actual runtime actions are no-ops for workers).
        row = layout.row(align=True)
        row.operator("blendersplitter.reset_runtime", icon="LOOP_BACK")
        row.operator("blendersplitter.hard_reset_runtime", icon="FILE_REFRESH")

        row = layout.row()
        row.enabled = not is_worker
        row.operator("blendersplitter.dry_run_integrity", icon="CHECKMARK")

        row = layout.row()
        row.enabled = not is_worker
        row.operator("blendersplitter.distributed_render", icon="RENDER_STILL")

        row = layout.row(align=True)
        row.enabled = not is_worker
        row.operator("blendersplitter.abort_render", icon="CANCEL")
        row.operator("blendersplitter.kick_all", icon="X")

        if is_worker:
            layout.label(text="Server-Aktionen im Worker-Modus deaktiviert", icon="INFO")

        layout.separator()
        _draw_cluster_monitor(layout, mgr)


class BLENDERSPLITTER_PT_tile_preview(bpy.types.Panel):
    bl_label = "Tile Preview"
    bl_idname = "BLENDERSPLITTER_PT_tile_preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Render"

    def draw(self, context):
        layout = self.layout
        cfg = context.scene.blendersplitter_settings
        mgr = _get_mgr()

        layout.label(text="Worker Count Preview")
        layout.prop(cfg, "worker_count")
        layout.prop(cfg, "tile_coefficient")
        layout.prop(cfg, "overlap_percent")

        layout.operator("blendersplitter.toggle_preview_overlay", icon="IMAGE_DATA")
        layout.operator("blendersplitter.render_partition_image", icon="RENDER_STILL")
        layout.operator("blendersplitter.close_partition_image", icon="TRASH")

        plan = _build_preview_plan(cfg, mgr)
        box = layout.box()
        box.label(text=f"Tiles: {len(plan)}")
        for item in plan[:16]:
            row = box.row(align=True)
            row.label(text=f"{item.get('tile_id')} -> {item.get('target')}")
            row.label(text=f"[{item.get('core_min_x')},{item.get('core_min_y')}] - [{item.get('core_max_x')},{item.get('core_max_y')}]")


class BLENDERSPLITTER_PT_sync_progress(bpy.types.Panel):
    bl_label = "Project Sync Progress"
    bl_idname = "BLENDERSPLITTER_PT_sync_progress"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Render"

    @classmethod
    def poll(cls, context):
        mgr = _get_mgr()
        return mgr is not None and (mgr.sync_active or bool(mgr.incoming_project_progress))

    def draw(self, context):
        mgr = _get_mgr()
        layout = self.layout

        pkg = mgr.sync_package_info or {}
        if pkg:
            layout.label(text=f"Package Files: {int(pkg.get('file_count', 0))}")
            layout.label(text=f"Package Chunks: {int(pkg.get('chunk_count', 0))}")
            layout.label(
                text=(
                    f"Source/Archive: {int(pkg.get('source_total_size', 0)) // (1024*1024)}MB / "
                    f"{int(pkg.get('archive_total_size', 0)) // (1024*1024)}MB"
                )
            )

        total = 0
        sent = 0
        for prog in mgr.sync_progress.values():
            total += int(prog.get("total_bytes", 0))
            sent += int(prog.get("current_bytes", 0))
        if total <= 0 and mgr.incoming_project_progress:
            total = int(mgr.incoming_project_progress.get("total_bytes", 0))
            sent = int(mgr.incoming_project_progress.get("current_bytes", 0))
        pct = (float(sent) / float(total) * 100.0) if total else 0.0
        layout.label(text=f"Overall: {pct:.1f}%")
        elapsed = max(0.01, time.time() - mgr.sync_start_time)
        speed = (sent / (1024 * 1024)) / elapsed
        layout.label(text=f"Sent: {sent // (1024*1024)}MB / {total // (1024*1024)}MB @ {speed:.1f}MB/s")

        for worker_id, state in sorted((mgr.worker_sync_state or {}).items()):
            cur = int(state.get("current_bytes", 0))
            wt = int(state.get("total_bytes", 0))
            w_pct = (float(cur) / float(wt) * 100.0) if wt else 0.0
            layout.label(text=f"{worker_id[:8]}: {state.get('phase', 'idle')} {w_pct:.1f}%")

        if mgr.incoming_project_progress:
            rp = mgr.incoming_project_progress
            layout.label(
                text=(
                    f"Worker Download: {int(rp.get('received_chunks', 0))}/"
                    f"{int(rp.get('total_chunks', 0))} Chunks"
                )
            )


CLASSES = (
    BLENDERSPLITTER_PG_settings_v2,
    BLENDERSPLITTER_OT_reset_runtime,
    BLENDERSPLITTER_OT_hard_reset_runtime,
    BLENDERSPLITTER_OT_start_network,
    BLENDERSPLITTER_OT_stop_network,
    BLENDERSPLITTER_OT_start_server,
    BLENDERSPLITTER_OT_cluster_monitor_popup,
    BLENDERSPLITTER_OT_install_requirements,
    BLENDERSPLITTER_OT_clean_worker_blends,
    BLENDERSPLITTER_OT_sync_project_files,
    BLENDERSPLITTER_OT_dry_run_integrity,
    BLENDERSPLITTER_OT_distributed_render,
    BLENDERSPLITTER_OT_abort_render,
    BLENDERSPLITTER_OT_kick_all,
    BLENDERSPLITTER_OT_toggle_preview_overlay,
    BLENDERSPLITTER_OT_render_partition_image,
    BLENDERSPLITTER_OT_close_partition_image,
    BLENDERSPLITTER_PT_panel,
    BLENDERSPLITTER_PT_tile_preview,
    BLENDERSPLITTER_PT_sync_progress,
)


def register():
    try:
        if hasattr(bpy.types.Scene, "blendersplitter_settings"):
            del bpy.types.Scene.blendersplitter_settings
    except Exception:
        pass

    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blendersplitter_settings = bpy.props.PointerProperty(type=BLENDERSPLITTER_PG_settings_v2)

    global _ui_refresh_registered
    if not _ui_refresh_registered:
        bpy.app.timers.register(_ui_refresh_tick, persistent=True)
        _ui_refresh_registered = True

    global _camera_border_handler
    if _camera_border_handler is None:
        _camera_border_handler = bpy.types.SpaceView3D.draw_handler_add(_draw_camera_border_callback, (), "WINDOW", "POST_PIXEL")


def unregister():
    if hasattr(bpy.types.Scene, "blendersplitter_settings"):
        del bpy.types.Scene.blendersplitter_settings

    global _preview_handler
    if _preview_handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_preview_handler, "WINDOW")
        except Exception:
            pass
        _preview_handler = None

    global _camera_border_handler
    if _camera_border_handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_camera_border_handler, "WINDOW")
        except Exception:
            pass
        _camera_border_handler = None

    global _ui_refresh_registered
    _ui_refresh_registered = False

    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
