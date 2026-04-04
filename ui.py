import math
import os
import tempfile
import time
import uuid

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .tiles import generate_tiles, grid_for_worker_count, overlap_pixels
from .worker import manager

_preview_handler = None
_camera_border_handler = None
_ui_refresh_registered = False


def _ui_refresh_tick():
    if not _ui_refresh_registered:
        return None
    try:
        mgr = manager()
        if mgr.started or mgr.sync_active or mgr.pending_jobs:
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
    if mgr.render_plan:
        return mgr.render_plan

    scene = bpy.context.scene
    render = scene.render
    res_x = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
    res_y = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))

    total_nodes = cfg.worker_count + (1 if cfg.server_render_tiles else 0)
    grid_x, grid_y = grid_for_worker_count(max(1, total_nodes))
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
        mgr = manager()
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

        mgr = manager()
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
        cfg = context.scene.blendersplitter_settings
        mgr = manager()
        mgr.configure(
            cfg.host,
            cfg.server_port,
            cfg.discovery_port,
            cfg.overlap_percent,
            cfg.max_retries,
            cfg.auto_sync_project,
            cfg.show_render_window,
            cfg.server_render_tiles,
            cfg.output_dir,
        )
        mgr.start()
        self.report({"INFO"}, "Cluster gestartet")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_stop_network(bpy.types.Operator):
    bl_idname = "blendersplitter.stop_network"
    bl_label = "Stop Cluster"

    def execute(self, context):
        manager().stop()
        self.report({"INFO"}, "Cluster gestoppt")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_start_server(bpy.types.Operator):
    bl_idname = "blendersplitter.start_server"
    bl_label = "Force Server"

    def execute(self, context):
        cfg = context.scene.blendersplitter_settings
        mgr = manager()
        mgr.configure(
            cfg.host,
            cfg.server_port,
            cfg.discovery_port,
            cfg.overlap_percent,
            cfg.max_retries,
            cfg.auto_sync_project,
            cfg.show_render_window,
            cfg.server_render_tiles,
            cfg.output_dir,
        )
        ok = mgr.force_start_server()
        if ok:
            self.report({"INFO"}, "Server gestartet")
            return {"FINISHED"}
        self.report({"ERROR"}, mgr.last_error or "Serverstart fehlgeschlagen")
        return {"CANCELLED"}


class BLENDERSPLITTER_OT_cluster_monitor_popup(bpy.types.Operator):
    bl_idname = "blendersplitter.cluster_monitor_popup"
    bl_label = "Cluster Monitor"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        _draw_cluster_monitor(self.layout, manager())


class BLENDERSPLITTER_OT_install_requirements(bpy.types.Operator):
    bl_idname = "blendersplitter.install_requirements"
    bl_label = "Install Requirements"

    def execute(self, context):
        ok = manager().auto_install_requirements()
        if not ok:
            self.report({"ERROR"}, manager().last_error)
            return {"CANCELLED"}
        self.report({"INFO"}, "Requirements installiert")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_dry_run_integrity(bpy.types.Operator):
    bl_idname = "blendersplitter.dry_run_integrity"
    bl_label = "Dry Run Integrity"

    def execute(self, context):
        ok = manager().run_integrity_check(timeout_seconds=5.0)
        if not ok:
            self.report({"ERROR"}, manager().status)
            return {"CANCELLED"}
        self.report({"INFO"}, manager().status)
        return {"FINISHED"}


class BLENDERSPLITTER_OT_sync_project_files(bpy.types.Operator):
    bl_idname = "blendersplitter.sync_project_files"
    bl_label = "Sync Project Files"

    def execute(self, context):
        ok = manager().sync_project_files(timeout_seconds=180.0)
        if not ok:
            self.report({"ERROR"}, manager().last_error or manager().status)
            return {"CANCELLED"}
        self.report({"INFO"}, manager().status)
        return {"FINISHED"}


class BLENDERSPLITTER_OT_distributed_render(bpy.types.Operator):
    bl_idname = "blendersplitter.distributed_render"
    bl_label = "Distributed Render"

    def execute(self, context):
        ok = manager().start_distributed_render()
        if not ok:
            self.report({"ERROR"}, manager().last_error or "Render konnte nicht gestartet werden")
            return {"CANCELLED"}
        self.report({"INFO"}, "Render gestartet")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_abort_render(bpy.types.Operator):
    bl_idname = "blendersplitter.abort_render"
    bl_label = "Abort Render"

    def execute(self, context):
        if not manager().cancel_render():
            self.report({"ERROR"}, manager().status)
            return {"CANCELLED"}
        self.report({"INFO"}, "Render abgebrochen")
        return {"FINISHED"}


class BLENDERSPLITTER_OT_kick_all(bpy.types.Operator):
    bl_idname = "blendersplitter.kick_all"
    bl_label = "Kick All Workers"

    def execute(self, context):
        manager().kick_all_workers()
        self.report({"INFO"}, "Alle Worker getrennt")
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
        mgr = manager()
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


class BLENDERSPLITTER_PG_settings(bpy.types.PropertyGroup):
    host: bpy.props.StringProperty(name="Host", default="0.0.0.0")
    server_port: bpy.props.IntProperty(name="Server Port", default=8765, min=1024, max=65535)
    discovery_port: bpy.props.IntProperty(name="Discovery Port", default=8766, min=1024, max=65535)
    overlap_percent: bpy.props.FloatProperty(name="Overlap %", default=3.0, min=2.0, max=8.0)
    worker_count: bpy.props.IntProperty(name="Worker Count", default=4, min=1, max=256)
    max_retries: bpy.props.IntProperty(name="Max Retries", default=3, min=1, max=20)
    auto_sync_project: bpy.props.BoolProperty(name="Auto Sync Project", default=False)
    show_render_window: bpy.props.BoolProperty(name="Show Render Window", default=True)
    server_render_tiles: bpy.props.BoolProperty(name="Server Render Tiles", default=True)
    output_dir: bpy.props.StringProperty(name="Output Folder", subtype="DIR_PATH", default="")


class BLENDERSPLITTER_PT_panel(bpy.types.Panel):
    bl_label = "Blender Splitter"
    bl_idname = "BLENDERSPLITTER_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Render"

    def draw(self, context):
        layout = self.layout
        cfg = context.scene.blendersplitter_settings
        mgr = manager()
        is_worker = mgr.role == "worker"

        layout.label(text="Cluster Configuration")
        layout.prop(cfg, "host")
        layout.prop(cfg, "server_port")
        layout.prop(cfg, "discovery_port")
        layout.prop(cfg, "output_dir")
        layout.prop(cfg, "overlap_percent")
        layout.prop(cfg, "worker_count")
        layout.prop(cfg, "max_retries")
        layout.prop(cfg, "auto_sync_project")
        layout.prop(cfg, "server_render_tiles")
        layout.prop(cfg, "show_render_window")

        row = layout.row(align=True)
        row.operator("blendersplitter.start_network", icon="PLAY")
        row.operator("blendersplitter.stop_network", icon="PAUSE")

        layout.operator("blendersplitter.start_server", icon="NETWORK_DRIVE")
        layout.operator("blendersplitter.cluster_monitor_popup", icon="WINDOW")

        layout.separator()
        layout.operator("blendersplitter.install_requirements", icon="CONSOLE")
        row = layout.row()
        row.enabled = not is_worker
        row.operator("blendersplitter.sync_project_files", icon="FILE_REFRESH")

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
            layout.label(text="Server-Aktionen auf Worker gesperrt")

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
        mgr = manager()

        layout.label(text="Preview & Overlap")
        layout.prop(cfg, "worker_count")
        layout.prop(cfg, "overlap_percent")

        layout.operator("blendersplitter.toggle_preview_overlay", icon="IMAGE_DATA")
        layout.operator("blendersplitter.render_partition_image", icon="RENDER_STILL")
        layout.operator("blendersplitter.close_partition_image", icon="TRASH")

        plan = _build_preview_plan(cfg, mgr)
        layout.label(text=f"Tiles: {len(plan)}")
        for item in plan[:16]:
            row = layout.row(align=True)
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
        mgr = manager()
        return mgr.sync_active or bool(mgr.incoming_project_progress)

    def draw(self, context):
        mgr = manager()
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
    BLENDERSPLITTER_PG_settings,
    BLENDERSPLITTER_OT_start_network,
    BLENDERSPLITTER_OT_stop_network,
    BLENDERSPLITTER_OT_start_server,
    BLENDERSPLITTER_OT_cluster_monitor_popup,
    BLENDERSPLITTER_OT_install_requirements,
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
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blendersplitter_settings = bpy.props.PointerProperty(type=BLENDERSPLITTER_PG_settings)

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
