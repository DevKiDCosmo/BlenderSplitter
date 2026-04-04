import bpy
import time
import gpu
import blf
from gpu_extras.batch import batch_for_shader
from gpu.types import GPUBatch
import math
import uuid
import os
import tempfile

from .worker import manager
from .tiles import generate_tiles, grid_for_worker_count, overlap_pixels


def _draw_cluster_monitor(layout, mgr):
	workers = len(mgr.connected_workers)
	server_ready = bool(mgr.role == "server" and mgr.started)
	worker_ready = bool(mgr.role == "worker" and (mgr.received_project_dir or mgr.pending_project_load) and not mgr.last_error)
	network_ok = bool(mgr.started and (mgr.role in {"server", "worker"}))

	box = layout.box()
	box.label(text="Cluster Monitor")
	box.label(text=f"Netzwerk: {'OK' if network_ok else 'WARTET'}")
	box.label(text=f"Role: {mgr.role}")
	box.label(text=f"Server Ready: {'JA' if server_ready else 'NEIN'}")
	box.label(text=f"Worker Ready: {'JA' if worker_ready else 'NEIN'}")
	box.label(text=f"Workers verbunden: {workers}")
	box.label(text=f"Endpoint: {mgr.server_host}:{mgr.server_port}")
	box.label(text=f"Status: {mgr.status}")

	if mgr.sync_active or mgr.sync_progress:
		total_bytes = mgr.sync_total_bytes or sum(p.get("total_bytes", 0) for p in mgr.sync_progress.values())
		sent_bytes = sum(p.get("current_bytes", 0) for p in mgr.sync_progress.values())
		pct = (sent_bytes / total_bytes * 100.0) if total_bytes else 0.0
		sync_box = layout.box()
		sync_box.label(text="Projekt Sync")
		sync_box.label(text=f"Fortschritt: {pct:.1f}%")
		sync_box.label(text=f"Daten: {sent_bytes // (1024 * 1024):.0f} MB / {total_bytes // (1024 * 1024):.0f} MB")
		for wid, prog in mgr.sync_progress.items():
			sync_box.label(text=f"{wid[:12]}: {prog.get('status', 'n/a')} ({prog.get('part', 0)}/{prog.get('total_parts', 0)})")

	if mgr.render_plan:
		plan_box = layout.box()
		plan_box.label(text="Tile Plan")
		for item in mgr.render_plan:
			plan_box.label(text=(
				f"{item.get('tile_id')} -> {item.get('target')} "
				f"[{item.get('min_x')},{item.get('min_y')}]"
				f"-[{item.get('max_x')},{item.get('max_y')}]"
			))

	if mgr.last_error:
		err = layout.box()
		err.label(text=f"Last Error: {mgr.last_error}")


class BLENDERSPLITTER_OT_start_network(bpy.types.Operator):
	bl_idname = "blendersplitter.start_network"
	bl_label = "Start Cluster"
	bl_description = "Startet Discovery + Server/Worker Verbindung"

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
		self.report({"INFO"}, "Distributed Render Netzwerk gestartet")
		return {'FINISHED'}


class BLENDERSPLITTER_OT_stop_network(bpy.types.Operator):
	bl_idname = "blendersplitter.stop_network"
	bl_label = "Stop Cluster"
	bl_description = "Stoppt Server/Worker Kommunikation"

	def execute(self, context):
		manager().stop()
		self.report({"INFO"}, "Distributed Render Netzwerk gestoppt")
		return {'FINISHED'}


class BLENDERSPLITTER_OT_start_server(bpy.types.Operator):
	bl_idname = "blendersplitter.start_server"
	bl_label = "Start Server"
	bl_description = "Erzwingt lokalen Server-Modus"

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
		mgr.force_start_server()
		self.report({"INFO"}, "Server-Start angefordert")
		return {'FINISHED'}


class BLENDERSPLITTER_OT_cluster_monitor_popup(bpy.types.Operator):
	bl_idname = "blendersplitter.cluster_monitor_popup"
	bl_label = "Cluster Monitor"
	bl_description = "Live Monitor mit Verbindungs- und Renderstatus"

	def invoke(self, context, event):
		return context.window_manager.invoke_props_dialog(self, width=560)

	def draw(self, context):
		_draw_cluster_monitor(self.layout, manager())


class BLENDERSPLITTER_OT_install_requirements(bpy.types.Operator):
	bl_idname = "blendersplitter.install_requirements"
	bl_label = "Install Requirements"
	bl_description = "Installiert fehlende Python-Pakete automatisch im Blender Python"

	def execute(self, context):
		ok = manager().auto_install_requirements()
		if not ok:
			self.report({"ERROR"}, manager().last_error)
			return {'CANCELLED'}
		self.report({"INFO"}, "Requirements installiert")
		return {'FINISHED'}


class BLENDERSPLITTER_OT_dry_run_integrity(bpy.types.Operator):
	bl_idname = "blendersplitter.dry_run_integrity"
	bl_label = "Dry Run Integrity Check"
	bl_description = "Validiert vor Render die Konfiguration auf allen verbundenen Workern"

	def execute(self, context):
		ok = manager().run_integrity_check(timeout_seconds=5.0)
		if not ok:
			self.report({"ERROR"}, manager().status)
			return {'CANCELLED'}
		self.report({"INFO"}, manager().status)
		return {'FINISHED'}


class BLENDERSPLITTER_OT_distributed_render(bpy.types.Operator):
	bl_idname = "blendersplitter.distributed_render"
	bl_label = "Distributed Render"
	bl_description = "Startet Tile-Render auf allen verbundenen Maschinen"

	def execute(self, context):
		ok = manager().start_distributed_render()
		if not ok:
			self.report({"ERROR"}, manager().last_error or "Render konnte nicht gestartet werden")
			return {'CANCELLED'}
		if manager().sync_active:
			bpy.ops.blendersplitter.sync_progress_popup('INVOKE_DEFAULT')
		self.report({"INFO"}, "Distributed Render gestartet")
		return {'FINISHED'}


class BLENDERSPLITTER_OT_sync_progress_popup(bpy.types.Operator):
	bl_idname = "blendersplitter.sync_progress_popup"
	bl_label = "Project Sync Progress"
	bl_description = "Zeigt den aktuellen Projekt-Sync in einem separaten Fenster an"

	def invoke(self, context, event):
		return context.window_manager.invoke_popup(self, width=420)

	def draw(self, context):
		mgr = manager()
		layout = self.layout
		layout.label(text="Project Sync Progress")
		layout.label(text=f"Status: {mgr.status}")
		total_bytes = mgr.sync_total_bytes
		sent_bytes = sum(progress.get("current_bytes", 0) for progress in mgr.sync_progress.values())
		layout.label(text=f"Workers: {len(mgr.sync_progress)}")
		layout.label(text=f"Sent: {sent_bytes // (1024 * 1024):.0f} MB / {total_bytes // (1024 * 1024):.0f} MB")
		if mgr.render_plan:
			plan_box = layout.box()
			plan_box.label(text="Render Plan")
			for item in mgr.render_plan:
				plan_box.label(text=(
					f"{item.get('tile_id')} -> {item.get('target')} "
					f"[{item.get('core_min_x')},{item.get('core_min_y')}]"
					f"-[{item.get('core_max_x')},{item.get('core_max_y')}]"
				))
		for worker_id, progress in mgr.sync_progress.items():
			box = layout.box()
			box.label(text=f"Worker: {worker_id[:16]}")
			box.label(text=f"State: {progress.get('status', 'unknown')}")
			current = progress.get("current_bytes", 0)
			total = progress.get("total_bytes", 0)
			pct = (current / total) if total else 0.0
			box.label(text=f"Progress: {pct * 100:.1f}%")
			box.label(text=f"Bytes: {current // (1024 * 1024):.0f}MB / {total // (1024 * 1024):.0f}MB")
			if progress.get("error"):
				box.label(text=f"Error: {progress.get('error')}")


class BLENDERSPLITTER_OT_worker_sync_popup(bpy.types.Operator):
	bl_idname = "blendersplitter.worker_sync_popup"
	bl_label = "Worker Download Progress"
	bl_description = "Zeigt den Downloadfortschritt des eingehenden Projekts am Worker an"

	def invoke(self, context, event):
		return context.window_manager.invoke_props_dialog(self, width=440)

	def draw(self, context):
		mgr = manager()
		layout = self.layout
		progress = mgr.incoming_project_progress or {}
		layout.label(text="Worker Download Progress")
		layout.label(text=f"Status: {progress.get('status', mgr.status)}")
		current = int(progress.get("current_bytes", 0))
		total = int(progress.get("total_bytes", 0))
		percent = (current / total * 100.0) if total else 0.0
		layout.label(text=f"Fortschritt: {percent:.1f}%")
		layout.label(text=f"Part: {int(progress.get('part_index', 0)) + 1}/{int(progress.get('total_parts', 1))}")
		layout.label(text=f"Daten: {current // (1024 * 1024):.0f} MB / {total // (1024 * 1024):.0f} MB")
		if progress.get("project_name"):
			layout.label(text=f"Projekt: {progress.get('project_name')}")
		if mgr.last_error:
			layout.label(text=f"Error: {mgr.last_error}")


class BLENDERSPLITTER_OT_worker_status_popup(bpy.types.Operator):
	bl_idname = "blendersplitter.worker_status_popup"
	bl_label = "Worker Status"
	bl_description = "Zeigt an, ob der Worker renderbereit ist"

	def invoke(self, context, event):
		return context.window_manager.invoke_props_dialog(self, width=420)

	def draw(self, context):
		mgr = manager()
		layout = self.layout
		can_render = bool(mgr.role == 'worker' and (mgr.received_project_dir or mgr.pending_project_load) and not mgr.last_error)
		layout.label(text="Worker Status")
		layout.label(text=f"Role: {mgr.role}")
		layout.label(text=f"Status: {mgr.status}")
		layout.label(text=f"Render bereit: {'Ja' if can_render else 'Nein'}")
		layout.label(text=f"Projekt empfangen: {'Ja' if mgr.received_project_dir else 'Nein'}")
		layout.label(text=f"Project load pending: {'Ja' if mgr.pending_project_load else 'Nein'}")
		if mgr.current_render_config:
			layout.label(text=(
				f"Render: {mgr.current_render_config.get('resolution_x', 0)}x"
				f"{mgr.current_render_config.get('resolution_y', 0)}"
			))
		if mgr.render_plan:
			box = layout.box()
			box.label(text="Tile Plan")
			for item in mgr.render_plan:
				box.label(text=(
					f"{item.get('tile_id')} -> {item.get('target')} "
					f"[{item.get('min_x')},{item.get('min_y')}]"
					f"-[{item.get('max_x')},{item.get('max_y')}]"
				))
		if mgr.last_integrity:
			layout.label(text=f"Integritaet: {mgr.last_integrity}")
		if mgr.last_error:
			layout.label(text=f"Error: {mgr.last_error}")


class BLENDERSPLITTER_OT_abort_render(bpy.types.Operator):
	bl_idname = "blendersplitter.abort_render"
	bl_label = "Abort Render"
	bl_description = "Abbricht den laufenden verteilten Render"

	def execute(self, context):
		ok = manager().cancel_render()
		if not ok:
			self.report({"ERROR"}, manager().status)
			return {'CANCELLED'}
		self.report({"INFO"}, "Distributed Render abgebrochen")
		return {'FINISHED'}


class BLENDERSPLITTER_OT_kick_all(bpy.types.Operator):
	bl_idname = "blendersplitter.kick_all"
	bl_label = "Kick All Workers"
	bl_description = "Trennt alle verbundenen Worker sofort"

	def execute(self, context):
		ok = manager().kick_all_workers()
		if not ok:
			self.report({"ERROR"}, manager().status)
			return {'CANCELLED'}
		self.report({"INFO"}, "Alle Worker getrennt")
		return {'FINISHED'}


class BLENDERSPLITTER_PG_settings(bpy.types.PropertyGroup):
	host: bpy.props.StringProperty(
		name="Host",
		description="Host IP fuer lokalen Server",
		default="0.0.0.0",
	)
	server_port: bpy.props.IntProperty(
		name="Server Port",
		default=8765,
		min=1024,
		max=65535,
	)
	overlap_percent: bpy.props.FloatProperty(
		name="Overlap %",
		description="Tile Overlap fuer besseres Stitching",
		default=3.0,
		min=2.0,
		max=5.0,
	)
	worker_count: bpy.props.IntProperty(
		name="Worker Count",
		description="Anzahl Worker fuer Preview/Simulation",
		default=4,
		min=1,
		max=128,
	)
	max_retries: bpy.props.IntProperty(
		name="Max Retries",
		description="Neu-Zuweisungsversuche pro Tile bei Worker-Ausfall",
		default=3,
		min=1,
		max=10,
	)
	auto_sync_project: bpy.props.BoolProperty(
		name="Auto Sync Project",
		description="Sendet Projektdateien automatisch an Worker vor dem Render",
		default=True,
	)
	show_render_window: bpy.props.BoolProperty(
		name="Show Render Window",
		description="Erzeugt ein Render-Fenster beim Add-on Start",
		default=True,
	)
	discovery_port: bpy.props.IntProperty(
		name="Discovery Port",
		default=8766,
		min=1024,
		max=65535,
	)
	server_render_tiles: bpy.props.BoolProperty(
		name="Server Render Tiles",
		description="Server rendert auch Tiles oder nur Orchestrierung",
		default=True,
	)
	output_dir: bpy.props.StringProperty(
		name="Output Folder",
		description="Basisordner fuer Render-Ergebnisse (master/raw-splits)",
		default="",
		subtype='DIR_PATH',
	)


class BLENDERSPLITTER_PT_panel(bpy.types.Panel):
	bl_label = "Blender Splitter"
	bl_idname = "BLENDERSPLITTER_PT_panel"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'Render'

	def draw(self, context):
		layout = self.layout
		cfg = context.scene.blendersplitter_settings
		mgr = manager()

		layout.label(text="Cluster Konfiguration")
		layout.prop(cfg, "host")
		layout.prop(cfg, "server_port")
		layout.prop(cfg, "discovery_port")
		layout.prop(cfg, "overlap_percent")
		layout.prop(cfg, "max_retries")
		layout.prop(cfg, "auto_sync_project")
		layout.prop(cfg, "show_render_window")
		layout.prop(cfg, "server_render_tiles")
		layout.prop(cfg, "worker_count")
		layout.prop(cfg, "output_dir")

		row = layout.row(align=True)
		row.operator("blendersplitter.start_network", icon='PLAY')
		row.operator("blendersplitter.stop_network", icon='PAUSE')
		layout.operator("blendersplitter.start_server", icon='NETWORK_DRIVE')
		layout.operator("blendersplitter.cluster_monitor_popup", icon='WINDOW')

		layout.separator()
		layout.operator("blendersplitter.install_requirements", icon='CONSOLE')
		layout.separator()
		layout.operator("blendersplitter.dry_run_integrity", icon='CHECKMARK')
		layout.separator()
		layout.operator("blendersplitter.distributed_render", icon='RENDER_STILL')

		row = layout.row(align=True)
		row.operator("blendersplitter.abort_render", icon='CANCEL')
		row.operator("blendersplitter.kick_all", icon='X')

		layout.separator()
		box = layout.box()
		box.label(text="Status / Metrics")
		box.label(text=f"Role: {mgr.role}")
		box.label(text=f"Server: {mgr.server_host}:{mgr.server_port}")
		box.label(text=f"Status: {mgr.status}")
		box.label(text=f"Integrity: {mgr.last_integrity}")
		box.label(text=f"Workers: {len(mgr.connected_workers)}")
		box.label(text=f"Render Time: {mgr.last_duration_seconds:.2f}s")
		box.label(text=f"Config: {cfg.host}:{cfg.server_port} / D:{cfg.discovery_port}")
		box.label(text=f"Overlap: {cfg.overlap_percent:.1f}% / Retries: {cfg.max_retries}")
		box.label(text=f"Project Sync: {'ON' if cfg.auto_sync_project else 'OFF'}")
		if cfg.output_dir:
			box.label(text=f"Output Base: {bpy.path.abspath(cfg.output_dir)}")
		if getattr(mgr, "current_output_root", ""):
			box.label(text=f"Run Dir: {mgr.current_output_root}")
		if getattr(mgr, "current_master_dir", ""):
			box.label(text=f"Master Dir: {mgr.current_master_dir}")
		if getattr(mgr, "current_raw_splits_dir", ""):
			box.label(text=f"Raw Splits: {mgr.current_raw_splits_dir}")
		stats = getattr(mgr, "transfer_stats", {}) or {}
		box.label(
			text=(
				f"Transfer inline/chunked: {int(stats.get('tiles_inline', 0))}/"
				f"{int(stats.get('tiles_chunked', 0))}"
			)
		)
		if mgr.received_project_dir:
			box.label(text=f"Project Dir: {mgr.received_project_dir}")
		if mgr.last_error:
			box.label(text=f"Error: {mgr.last_error}")


# --- Preview overlay support -------------------------------------------------
_preview_handler = None

def _color_for_target(target):
	if target == "MASTER":
		return (0.1, 0.8, 0.1, 0.6)
	# simple hash to color for worker id
	try:
		h = abs(hash(str(target)))
		r = ((h >> 0) & 0xFF) / 255.0
		g = ((h >> 8) & 0xFF) / 255.0
		b = ((h >> 16) & 0xFF) / 255.0
		return (0.2 + 0.6 * r, 0.2 + 0.6 * g, 0.2 + 0.6 * b, 0.5)
	except Exception:
		return (0.5, 0.5, 0.5, 0.5)


def _draw_preview_callback():
	try:
		context = bpy.context
		region = context.region
		width = region.width
		height = region.height
		mgr = manager()

		# small preview rect in top-right
		box_w = 220
		box_h = 220
		margin = 16
		px0 = width - box_w - margin
		py0 = height - box_h - margin
		px1 = px0 + box_w
		py1 = py0 + box_h

		# background
		shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
		verts = [(px0, py0), (px1, py0), (px1, py1), (px0, py1)]
		batch = batch_for_shader(shader, 'TRIS', {"pos": [verts[0], verts[1], verts[2], verts[0], verts[2], verts[3]]})
		shader.bind()
		shader.uniform_float("color", (0.05, 0.05, 0.05, 0.85))
		batch.draw(shader)

		# border
		border_verts = [(px0, py0), (px1, py0), (px1, py1), (px0, py1), (px0, py0)]
		batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": border_verts})
		shader.uniform_float("color", (0.8, 0.8, 0.8, 0.6))
		batch.draw(shader)

		# build a plan: use active render_plan if present, otherwise simulate from scene + settings
		if mgr.render_plan:
			plan = mgr.render_plan
			res_x = mgr.current_render_config.get("resolution_x") if mgr.current_render_config else None
			res_y = mgr.current_render_config.get("resolution_y") if mgr.current_render_config else None
			if not res_x or not res_y:
				max_x = max((item.get("max_x", 0) for item in plan), default=1)
				max_y = max((item.get("max_y", 0) for item in plan), default=1)
				res_x = max(res_x or 0, max_x)
				res_y = max(res_y or 0, max_y)
		else:
			# simulate tiles from current scene and settings
			try:
				scene = bpy.context.scene
				render = scene.render
				final_width = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
				final_height = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))
				cfg = scene.blendersplitter_settings
				total_nodes = cfg.worker_count + (1 if cfg.server_render_tiles else 0)
				grid_x, grid_y = grid_for_worker_count(max(1, total_nodes))
				overlap_px = overlap_pixels(final_width, final_height, cfg.overlap_percent)
				tiles = generate_tiles(final_width, final_height, grid_x, grid_y, overlap=overlap_px)
				targets = []
				if cfg.server_render_tiles:
					targets.append("MASTER")
				for i in range(cfg.worker_count):
					targets.append(f"W{i+1}")
				plan = []
				for idx, tile in enumerate(tiles):
					target = targets[idx % len(targets)] if targets else "MASTER"
					plan.append({
						"tile_id": tile.get("id"),
						"target": target,
						"min_x": tile.get("min_x"),
						"max_x": tile.get("max_x"),
						"min_y": tile.get("min_y"),
						"max_y": tile.get("max_y"),
						"core_min_x": tile.get("core_min_x"),
						"core_max_x": tile.get("core_max_x"),
						"core_min_y": tile.get("core_min_y"),
						"core_max_y": tile.get("core_max_y"),
					})
				res_x = final_width
				res_y = final_height
			except Exception:
				blf.position(0, px0 + 8, py1 - 20, 0)
				blf.size(0, 14, 72)
				blf.draw(0, "No scene or settings for preview")
				return

		for item in plan:
			try:
				min_x = float(item.get("min_x", 0))
				max_x = float(item.get("max_x", 0))
				min_y = float(item.get("min_y", 0))
				max_y = float(item.get("max_y", 0))
				nx0 = min_x / max(1.0, float(res_x))
				nx1 = max_x / max(1.0, float(res_x))
				ny0 = min_y / max(1.0, float(res_y))
				ny1 = max_y / max(1.0, float(res_y))

				x0 = px0 + nx0 * box_w
				x1 = px0 + nx1 * box_w
				# invert Y for UI coords
				y0 = py0 + (1.0 - ny1) * box_h
				y1 = py0 + (1.0 - ny0) * box_h

				color = _color_for_target(item.get("target"))
				rect_verts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
				quad = [rect_verts[0], rect_verts[1], rect_verts[2], rect_verts[0], rect_verts[2], rect_verts[3]]
				batch = batch_for_shader(shader, 'TRIS', {"pos": quad})
				shader.uniform_float("color", color)
				batch.draw(shader)

				# outline
				outline = [rect_verts[0], rect_verts[1], rect_verts[2], rect_verts[3], rect_verts[0]]
				batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": outline})
				shader.uniform_float("color", (0.95, 0.95, 0.95, 0.9))
				batch.draw(shader)

				# draw id text
				blf.position(0, x0 + 3, y1 - 14, 0)
				blf.size(0, 11, 72)
				tid = str(item.get("tile_id"))[:10]
				ttxt = f"{tid}({str(item.get('target'))[:6]})"
				blf.draw(0, ttxt)
			except Exception:
				continue
	except Exception:
		return


class BLENDERSPLITTER_OT_toggle_preview_overlay(bpy.types.Operator):
	bl_idname = "blendersplitter.toggle_preview_overlay"
	bl_label = "Toggle Preview Overlay"
	bl_description = "Zeigt eine kleine Vorschau des Tile-Layouts im 3D-Viewport an"

	def execute(self, context):
		global _preview_handler
		if _preview_handler is None:
			_preview_handler = bpy.types.SpaceView3D.draw_handler_add(_draw_preview_callback, (), 'WINDOW', 'POST_PIXEL')
			self.report({'INFO'}, 'Preview Overlay aktiviert')
		else:
			try:
				bpy.types.SpaceView3D.draw_handler_remove(_preview_handler, 'WINDOW')
			except Exception:
				pass
			_preview_handler = None
			self.report({'INFO'}, 'Preview Overlay deaktiviert')
		return {'FINISHED'}


def _create_partition_image(plan, res_x, res_y, max_dim=1024):
	"""Create a Blender image visualizing tile partitions. Returns bpy.types.Image."""
	# scale to max_dim while preserving aspect
	scale = min(1.0, float(max_dim) / max(res_x, res_y)) if max(res_x, res_y) > 0 else 1.0
	w = max(1, int(res_x * scale))
	h = max(1, int(res_y * scale))

	name = f"BlenderSplitter_Partition_{uuid.uuid4().hex[:8]}"
	img = bpy.data.images.new(name, width=w, height=h, alpha=True, float_buffer=False)
	# initialize transparent
	pixels = [0.0] * (w * h * 4)

	def set_rect(x0, y0, x1, y1, color):
		# clamp
		ix0 = max(0, min(w - 1, int(math.floor(x0))))
		ix1 = max(0, min(w, int(math.ceil(x1))))
		iy0 = max(0, min(h - 1, int(math.floor(y0))))
		iy1 = max(0, min(h, int(math.ceil(y1))))
		for yy in range(iy0, iy1):
			for xx in range(ix0, ix1):
				idx = (yy * w + xx) * 4
				pixels[idx] = color[0]
				pixels[idx + 1] = color[1]
				pixels[idx + 2] = color[2]
				pixels[idx + 3] = color[3]

	# fill tiles
	for item in plan:
		try:
			min_x = float(item.get('min_x', 0))
			max_x = float(item.get('max_x', 0))
			min_y = float(item.get('min_y', 0))
			max_y = float(item.get('max_y', 0))
			# normalized to res
			nx0 = min_x / max(1.0, float(res_x))
			nx1 = max_x / max(1.0, float(res_x))
			ny0 = min_y / max(1.0, float(res_y))
			ny1 = max_y / max(1.0, float(res_y))
			x0 = nx0 * w
			x1 = nx1 * w
			# invert Y: image origin is bottom-left
			y0 = (1.0 - ny1) * h
			y1 = (1.0 - ny0) * h
			color = _color_for_target(item.get('target'))
			# premultiply slightly for visibility
			col = (color[0], color[1], color[2], min(1.0, color[3] + 0.2))
			set_rect(x0, y0, x1, y1, col)
		except Exception:
			continue

	img.pixels = pixels
	img.file_format = 'PNG'
	return img


class BLENDERSPLITTER_OT_render_partition_image(bpy.types.Operator):
	bl_idname = 'blendersplitter.render_partition_image'
	bl_label = 'Render Partition Image'
	bl_description = 'Erstellt ein PNG mit der Aufteilung der Tiles und öffnet es im Image Editor'

	def execute(self, context):
		mgr = manager()
		# build plan similar to preview: prefer active render_plan
		if mgr.render_plan:
			plan = mgr.render_plan
			res_x = mgr.current_render_config.get('resolution_x') if mgr.current_render_config else None
			res_y = mgr.current_render_config.get('resolution_y') if mgr.current_render_config else None
			if not res_x or not res_y:
				max_x = max((item.get('max_x', 0) for item in plan), default=1)
				max_y = max((item.get('max_y', 0) for item in plan), default=1)
				res_x = max(res_x or 0, max_x)
				res_y = max(res_y or 0, max_y)
		else:
			# simulate
			try:
				scene = bpy.context.scene
				render = scene.render
				res_x = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
				res_y = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))
				cfg = scene.blendersplitter_settings
				total_nodes = cfg.worker_count + (1 if cfg.server_render_tiles else 0)
				grid_x, grid_y = grid_for_worker_count(max(1, total_nodes))
				overlap_px = overlap_pixels(res_x, res_y, cfg.overlap_percent)
				tiles = generate_tiles(res_x, res_y, grid_x, grid_y, overlap=overlap_px)
				targets = []
				if cfg.server_render_tiles:
					targets.append('MASTER')
				for i in range(cfg.worker_count):
					targets.append(f'W{i+1}')
				plan = []
				for idx, tile in enumerate(tiles):
					target = targets[idx % len(targets)] if targets else 'MASTER'
					plan.append({
						'tile_id': tile.get('id'),
						'target': target,
						'min_x': tile.get('min_x'),
						'max_x': tile.get('max_x'),
						'min_y': tile.get('min_y'),
						'max_y': tile.get('max_y'),
					})
			except Exception as exc:
				self.report({'ERROR'}, f'Vorschau konnte nicht erzeugt werden: {exc}')
				return {'CANCELLED'}

		img = _create_partition_image(plan, res_x, res_y, max_dim=1024)

		# save to temp PNG to ensure bpy.ops.image.open works reliably
		tmp_dir = tempfile.gettempdir()
		tmp_path = os.path.join(tmp_dir, f"{img.name}.png")
		try:
			img.filepath_raw = tmp_path
			img.file_format = 'PNG'
			img.save()
		except Exception:
			# fallback: ensure file removed if partially written
			try:
				if os.path.exists(tmp_path):
					os.remove(tmp_path)
			except Exception:
				pass

		wm = bpy.context.window_manager
		loaded_img = bpy.data.images.load(tmp_path, check_existing=True)
		wm["bl_splitter_partition_image"] = loaded_img.name
		wm["bl_splitter_partition_tmp_path"] = tmp_path

		# always create a new Blender window and use it as Image Editor
		window_count = len(wm.windows)
		bpy.ops.wm.window_new()
		new_window = wm.windows[-1] if len(wm.windows) > window_count else bpy.context.window
		if new_window and new_window.screen and new_window.screen.areas:
			area = new_window.screen.areas[0]
			area.type = 'IMAGE_EDITOR'
			area.spaces.active.image = loaded_img

		self.report({'INFO'}, f'Partition image erstellt: {img.name}')
		return {'FINISHED'}


class BLENDERSPLITTER_PT_tile_preview(bpy.types.Panel):
	bl_label = "Tile Preview"
	bl_idname = "BLENDERSPLITTER_PT_tile_preview"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'Render'

	def draw(self, context):
		layout = self.layout
		mgr = manager()
		cfg = context.scene.blendersplitter_settings

		layout.label(text="Tile Preview / Overlap")
		layout.prop(cfg, 'worker_count')
		layout.prop(cfg, 'overlap_percent')
		layout.operator('blendersplitter.toggle_preview_overlay', icon='IMAGE_DATA')
		layout.operator('blendersplitter.render_partition_image', icon='RENDER_STILL')
		layout.operator('blendersplitter.close_partition_image', icon='TRASH')

		# show active plan or simulate from scene/settings
		if mgr.render_plan:
			plan = mgr.render_plan
			worker_count = len(mgr.connected_workers)
		else:
			# simulate
			try:
				scene = bpy.context.scene
				render = scene.render
				final_width = max(1, int(render.resolution_x * (render.resolution_percentage / 100.0)))
				final_height = max(1, int(render.resolution_y * (render.resolution_percentage / 100.0)))
				total_nodes = cfg.worker_count + (1 if cfg.server_render_tiles else 0)
				grid_x, grid_y = grid_for_worker_count(max(1, total_nodes))
				overlap_px = overlap_pixels(final_width, final_height, cfg.overlap_percent)
				tiles = generate_tiles(final_width, final_height, grid_x, grid_y, overlap=overlap_px)
				targets = []
				if cfg.server_render_tiles:
					targets.append("MASTER")
				for i in range(cfg.worker_count):
					targets.append(f"W{i+1}")
				plan = []
				for idx, tile in enumerate(tiles):
					target = targets[idx % len(targets)] if targets else "MASTER"
					plan.append({
						"tile_id": tile.get("id"),
						"target": target,
						"core_min_x": tile.get("core_min_x"),
						"core_max_x": tile.get("core_max_x"),
						"core_min_y": tile.get("core_min_y"),
						"core_max_y": tile.get("core_max_y"),
					})
				worker_count = cfg.worker_count
			except Exception:
				layout.label(text='Kein Render-Plan und Szene nicht verfügbar')
				return

		layout.label(text=f"Tiles: {len(plan)} | Workers: {worker_count}")
		for item in plan:
			row = layout.row(align=True)
			tid = str(item.get('tile_id'))
			tgt = str(item.get('target'))
			core = f"[{item.get('core_min_x')},{item.get('core_min_y')}] - [{item.get('core_max_x')},{item.get('core_max_y')}]"
			row.label(text=f"{tid[:8]} -> {tgt[:8]}")
			row.label(text=core)


class BLENDERSPLITTER_OT_close_partition_image(bpy.types.Operator):
	bl_idname = 'blendersplitter.close_partition_image'
	bl_label = 'Close Partition Image'
	bl_description = 'Schliesst und entfernt das erzeugte Partition-Image'

	def execute(self, context):
		wm = bpy.context.window_manager
		img_name = wm.get('bl_splitter_partition_image')
		tmp_path = wm.get('bl_splitter_partition_tmp_path')
		removed = False
		if img_name and img_name in bpy.data.images:
			try:
				img = bpy.data.images[img_name]
				bpy.data.images.remove(img)
				removed = True
			except Exception:
				pass

		# remove temporary preview file
		if tmp_path:
			try:
				if os.path.exists(tmp_path):
					os.remove(tmp_path)
			except Exception:
				pass

		# cleanup wm keys
		for k in ('bl_splitter_partition_image', 'bl_splitter_partition_tmp_path'):
			if k in wm:
				del wm[k]

		if removed:
			self.report({'INFO'}, 'Partition image entfernt')
			return {'FINISHED'}
		else:
			self.report({'WARNING'}, 'Kein Partition image gefunden')
			return {'CANCELLED'}


class BLENDERSPLITTER_PT_sync_progress(bpy.types.Panel):
	"""Live Project Sync Progress Panel"""
	bl_label = "Project Sync Progress"
	bl_idname = "BLENDERSPLITTER_PT_sync_progress"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'Render'
	
	@classmethod
	def poll(cls, context):
		"""Only show when sync is active."""
		mgr = manager()
		return mgr.sync_active
	
	def draw(self, context):
		mgr = manager()
		layout = self.layout
		
		# Overall progress
		total_bytes = 0
		sent_bytes = 0
		for wid, prog in mgr.sync_progress.items():
			total_bytes += prog.get("total_bytes", 0)
			sent_bytes += prog.get("current_bytes", 0)
		
		if total_bytes > 0:
			overall_progress = sent_bytes / total_bytes
		else:
			overall_progress = 0.0
		
		box = layout.box()
		box.label(text=f"Overall Progress: {overall_progress * 100:.1f}%")
		
		# Calculate speed
		elapsed = max(0.01, time.time() - mgr.sync_start_time)
		speed_mbps = (sent_bytes / (1024 * 1024)) / elapsed if elapsed > 0 else 0
		
		box.label(text=f"Sent: {sent_bytes // (1024*1024):.0f}MB / {total_bytes // (1024*1024):.0f}MB @ {speed_mbps:.1f}MB/s")
		
		# Per-worker progress
		layout.separator()
		for worker_id, progress in mgr.sync_progress.items():
			worker_box = layout.box()
			worker_box.label(text=f"Worker: {worker_id[:16]}")
			
			# Status
			worker_box.label(text=f"Status: {progress.get('status', 'unknown')}")
			
			# Progress bar simulation
			current = progress.get("current_bytes", 0)
			total = progress.get("total_bytes", 0)
			if total > 0:
				pct = (current / total) * 100
			else:
				pct = 0
			
			worker_box.label(text=f"Progress: {pct:.1f}% ({current // (1024*1024):.0f} / {total // (1024*1024):.0f} MB)")
			
			# Speed & ETA
			speed = progress.get("speed_mbps", 0.0)
			worker_box.label(text=f"Speed: {speed:.1f} MB/s")
			
			if speed > 0.1 and total > current:
				remaining_mb = (total - current) / (1024 * 1024)
				eta_sec = remaining_mb / speed
				worker_box.label(text=f"ETA: {eta_sec:.0f}s")
			
			# Error message if present
			if progress.get("error"):
				error_box = worker_box.box()
				error_box.label(text=f"Error: {progress.get('error')}")


CLASSES = (
	BLENDERSPLITTER_PG_settings,
	BLENDERSPLITTER_OT_start_network,
	BLENDERSPLITTER_OT_stop_network,
	BLENDERSPLITTER_OT_start_server,
	BLENDERSPLITTER_OT_cluster_monitor_popup,
	BLENDERSPLITTER_OT_install_requirements,
	BLENDERSPLITTER_OT_dry_run_integrity,
	BLENDERSPLITTER_OT_distributed_render,
	BLENDERSPLITTER_OT_sync_progress_popup,
	BLENDERSPLITTER_OT_worker_sync_popup,
	BLENDERSPLITTER_OT_worker_status_popup,
	BLENDERSPLITTER_OT_abort_render,
	BLENDERSPLITTER_OT_kick_all,
	BLENDERSPLITTER_PT_panel,
	BLENDERSPLITTER_PT_sync_progress,
	# preview overlay operator + panel
	BLENDERSPLITTER_OT_toggle_preview_overlay,
	BLENDERSPLITTER_PT_tile_preview,
	BLENDERSPLITTER_OT_render_partition_image,
	BLENDERSPLITTER_OT_close_partition_image,
)


def register():
	for cls in CLASSES:
		bpy.utils.register_class(cls)
	bpy.types.Scene.blendersplitter_settings = bpy.props.PointerProperty(type=BLENDERSPLITTER_PG_settings)


def unregister():
	if hasattr(bpy.types.Scene, "blendersplitter_settings"):
		del bpy.types.Scene.blendersplitter_settings
	for cls in reversed(CLASSES):
		bpy.utils.unregister_class(cls)

	# remove preview handler if still present
	global _preview_handler
	try:
		if _preview_handler is not None:
			bpy.types.SpaceView3D.draw_handler_remove(_preview_handler, 'WINDOW')
			_preview_handler = None
	except Exception:
		pass
